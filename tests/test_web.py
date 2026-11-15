from __future__ import annotations

import base64
import datetime as dt
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from starlette.requests import Request
from starlette.testclient import TestClient

from mikiwame.pipeline import AnalysisFailed
from mikiwame.store.sql import WEB_BRANCH, Store
from mikiwame.web.app import WebApp, safe_report_html
from tests.conftest import make_settings


def _result(report: str, level: str):
    return SimpleNamespace(
        report=report,
        verdict=SimpleNamespace(level=SimpleNamespace(key=level)),
        case=SimpleNamespace(case_id="c1"),
    )


def _png(w: int = 40, h: int = 30) -> str:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def web(tmp_path):
    settings = make_settings(allowed_users="7", admin_chat_id=None, web_limit_visitor_daily=2)
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    analyzer = MagicMock()
    analyzer.analyze_draft = AsyncMock(return_value=_result("🔴 <b>Riesgo</b>", "red"))
    analyzer.unknown_link_only = MagicMock(return_value=None)
    return WebApp(settings, store, analyzer)


@pytest.fixture
def client(web):
    return TestClient(web.build(), client=("203.0.113.5", 5000))


def test_pages_are_served_with_a_strict_policy(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "¿Es timo?" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/privacidad").status_code == 200
    assert client.get("/app.js").headers["content-type"].startswith("text/javascript")
    # Nothing outside the page list, however it is spelled.
    assert client.get("/../.env").status_code == 404
    assert client.get("/index.html").status_code == 404


def test_analysis_returns_the_report_and_a_continue_token(web, client):
    r = client.post("/api/analyze", json={"text": "Te mando las llaves por Airbnb", "images": []})
    data = r.json()
    assert r.status_code == 200 and data["ok"]
    assert data["level"] == "red"
    assert data["report"] == "🔴 <b>Riesgo</b>"
    assert data["continue"]
    draft = web.analyzer.analyze_draft.call_args.args[0]
    assert draft.user_id < 0 and draft.texts() == ["Te mando las llaves por Airbnb"]


def test_the_advertisers_answer_joins_the_previous_case(web, client):
    first = client.post("/api/analyze", json={"text": "anuncio", "images": [_png()]}).json()
    client.post("/api/analyze", json={"text": "respuesta", "continue": first["continue"]})
    draft = web.analyzer.analyze_draft.call_args.args[0]
    assert draft.continues
    assert draft.texts() == ["anuncio", "respuesta"]
    assert len(draft.images()) == 1
    # A token is used once.
    r = client.post("/api/analyze", json={"text": "otra", "continue": first["continue"]})
    assert r.status_code == 410


def test_screenshots_are_reencoded_as_jpeg(web, client):
    client.post("/api/analyze", json={"text": "", "images": [_png(3000, 2000)]})
    (img,) = web.analyzer.analyze_draft.call_args.args[0].images()
    with Image.open(io.BytesIO(img)) as im:
        assert im.format == "JPEG" and max(im.size) <= 1600


def test_bad_input_is_refused_without_spending(web, client):
    assert client.post("/api/analyze", json={"text": " ", "images": []}).status_code == 400
    assert client.post("/api/analyze", content=b"not json").status_code == 400
    bad = base64.b64encode(b"not an image").decode()
    r = client.post("/api/analyze", json={"text": "x", "images": [bad]})
    assert r.status_code == 400 and "imágenes" in r.json()["error"]
    r = client.post("/api/analyze", json={"text": "x", "images": [_png()] * 9})
    assert r.status_code == 400
    web.analyzer.analyze_draft.assert_not_awaited()


def test_oversized_bodies_are_cut_before_parsing(web, client):
    web.settings = make_settings(web_max_image_mb=0, max_images=1)
    r = client.post("/api/analyze", content=b"x" * 400_000)
    assert r.status_code == 413
    web.analyzer.analyze_draft.assert_not_awaited()


async def test_a_visitor_who_leaves_mid_upload_is_not_an_error(web, caplog):
    async def receive():
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/analyze",
        "query_string": b"",
        "headers": [],
        "client": ("203.0.113.5", 5000),
    }
    with caplog.at_level(logging.INFO, logger="mikiwame.web.app"):
        r = await web.analyze(Request(scope, receive))
    assert r.status_code == 400
    assert "left before the upload finished" in caplog.text
    web.analyzer.analyze_draft.assert_not_awaited()


def test_daily_limit_per_connection(web, client):
    for _ in range(2):
        assert client.post("/api/analyze", json={"text": "a"}).status_code == 200
    r = client.post("/api/analyze", json={"text": "a"})
    assert r.status_code == 429 and "@EsTimoBot" in r.json()["error"]
    # Another address is another visitor.
    other = TestClient(web.build(), client=("198.51.100.9", 5000))
    assert other.post("/api/analyze", json={"text": "a"}).status_code == 200


def test_the_whole_website_has_its_own_daily_cap(web, client):
    web.settings = make_settings(web_limit_daily=1, web_limit_visitor_daily=5)
    web.limiter.settings = web.settings
    assert client.post("/api/analyze", json={"text": "a"}).status_code == 200
    other = TestClient(web.build(), client=("198.51.100.9", 5000))
    r = other.post("/api/analyze", json={"text": "a"})
    assert r.status_code == 429 and "web" in r.json()["error"]


def test_forwarded_for_is_believed_only_from_the_relay(web):
    web.settings = make_settings(web_trusted_proxies="100.64.0.10")
    relay = SimpleNamespace(client=SimpleNamespace(host="100.64.0.10"), headers={})
    relay.headers = {"x-forwarded-for": "10.0.0.1, 203.0.113.7"}
    assert web.client_ip(relay) == "203.0.113.7"
    direct = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.5"), headers={"x-forwarded-for": "1.2.3.4"}
    )
    assert web.client_ip(direct) == "203.0.113.5"
    assert web.visitor_id("203.0.113.7") == web.visitor_id("203.0.113.7") < 0
    assert web.visitor_id("203.0.113.7") != web.visitor_id("203.0.113.8")


def test_a_failed_analysis_is_explained(web, client):
    web.analyzer.analyze_draft = AsyncMock(side_effect=AnalysisFailed("No he podido leerlas."))
    r = client.post("/api/analyze", json={"text": "a"})
    assert r.status_code == 503 and r.json()["error"] == "No he podido leerlas."


def test_a_bare_unknown_link_is_not_charged(web, client):
    web.analyzer.unknown_link_only = MagicMock(return_value="🔗 Ese anuncio no lo tengo.")
    r = client.post("/api/analyze", json={"text": "https://www.idealista.com/inmueble/1/"})
    assert r.json()["level"] is None
    web.analyzer.analyze_draft.assert_not_awaited()
    assert web.store.count_usage_since(dt.datetime(2000, 1, 1, tzinfo=dt.UTC)) == 0


def test_events_are_logged_without_content_or_ip(client, caplog):
    with caplog.at_level(logging.INFO, logger="mikiwame.web.app"):
        client.post("/api/analyze", json={"text": "Mi IBAN ES9121000418450200051332"})
    lines = " ".join(r.getMessage() for r in caplog.records)
    assert "red report, delivered" in lines
    assert "IBAN" not in lines and "203.0.113.5" not in lines


def test_only_our_tags_survive_in_the_report():
    assert safe_report_html("<b>a</b> <i>b</i> <code>c</code>") == (
        "<b>a</b> <i>b</i> <code>c</code>"
    )
    assert safe_report_html('<img src=x onerror="alert(1)"><b>ok</b>') == (
        '&lt;img src=x onerror="alert(1)"&gt;<b>ok</b>'
    )
    assert safe_report_html("<b onclick=x>") == "&lt;b onclick=x&gt;"


def test_all_website_visitors_are_one_branch(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    out = store.branches({-5, -6, 9}, frozenset({1}))
    assert out[-5] == out[-6] == WEB_BRANCH
    assert out[9] == 9


def test_the_english_site_speaks_english(web, client):
    r = client.get("/en/")
    assert r.status_code == 200 and 'lang="en"' in r.text and "Analyse" in r.text
    assert client.get("/en/privacy").status_code == 200
    client.post("/api/analyze?lang=en", json={"text": "Send the deposit first"})
    assert web.analyzer.analyze_draft.call_args.args[1] == "en"
    r = client.post("/api/analyze?lang=en", json={"text": " "})
    assert r.json()["error"].startswith("Paste the listing")
    r = client.post("/api/analyze", json={"text": " "})
    assert r.json()["error"].startswith("Pega el anuncio")


def test_each_page_names_itself_and_its_translation(client):
    base = "https://estimo.patriciollorens.com"
    pairs = {
        "/": ("/", "/en/"),
        "/en/": ("/", "/en/"),
        "/privacidad": ("/privacidad", "/en/privacy"),
        "/en/privacy": ("/privacidad", "/en/privacy"),
    }
    for path, (es, en) in pairs.items():
        html = client.get(path).text
        assert f'<link rel="canonical" href="{base}{path}">' in html
        assert f'hreflang="es" href="{base}{es}"' in html
        assert f'hreflang="en" href="{base}{en}"' in html
        assert f'hreflang="x-default" href="{base}{es}"' in html


def test_the_example_is_served_without_spending(web, client):
    r = client.get("/api/example?lang=en")
    data = r.json()
    assert r.status_code == 200 and data["ok"] and data["level"] == "red"
    assert "Erasmus" in data["case"] and data["report"].startswith("🔴 <b>High risk")
    assert client.get("/api/example").json()["case"].startswith("Anuncio:")
    web.analyzer.analyze_draft.assert_not_awaited()


def test_guides_have_the_analyzer_and_their_twin_in_the_other_language(client):
    from mikiwame.web.guides import GUIDES, SITE

    for g in GUIDES:
        es, en = client.get(g.path_es), client.get(g.path_en)
        assert es.status_code == en.status_code == 200
        assert "default-src 'none'" in es.headers["content-security-policy"]
        for page, lang in ((es.text, "es"), (en.text, "en")):
            assert f'<html lang="{lang}">' in page
            assert f'hreflang="es" href="{SITE}{g.path_es}"' in page
            assert f'hreflang="en" href="{SITE}{g.path_en}"' in page
            assert 'id="form"' in page and 'id="example"' in page and "/app.js" in page
            assert "<title>" in page and '<meta name="description" content="' in page
            assert "{" not in page.split("<main>")[1]  # no placeholder left unfilled
    home = client.get("/").text
    assert all(f'href="{g.path_es}"' in home for g in GUIDES)
    assert all(f'href="{g.path_en}"' in client.get("/en/").text for g in GUIDES)


def test_the_example_has_a_page_to_share(client, web):
    # 27-10-2026: a link to the example sent on WhatsApp showed nothing (/ejemplo was a 404).
    for path, lang in (("/ejemplo", "es"), ("/en/example", "en")):
        r = client.get(path)
        assert r.status_code == 200 and "default-src 'none'" in r.headers["content-security-policy"]
        page = r.text
        assert f'<html lang="{lang}">' in page and 'id="form"' in page
        assert "DE89 3704 0044 0532 0130 00" in page  # the invented case, server-side
        assert '<div class="card result red">' in page
        assert "<script>" not in page.split("<main>")[1]
        assert "{" not in page.split("<main>")[1]
    site = "https://estimo.patriciollorens.com"
    assert f'hreflang="en" href="{site}/en/example"' in client.get("/ejemplo").text
    assert f"<loc>{site}/ejemplo</loc>" in client.get("/sitemap.xml").text
    web.analyzer.analyze_draft.assert_not_awaited()


def test_search_engines_get_a_sitemap_and_robots(client):
    from mikiwame.web.guides import GUIDES, SITE

    sm = client.get("/sitemap.xml")
    assert sm.headers["content-type"].startswith("application/xml")
    for g in GUIDES:
        assert f"<loc>{SITE}{g.path_es}</loc>" in sm.text
        assert f"<loc>{SITE}{g.path_en}</loc>" in sm.text
    assert f"<loc>{SITE}/en/</loc>" in sm.text
    robots = client.get("/robots.txt").text
    assert "Disallow: /api/" in robots and f"Sitemap: {SITE}/sitemap.xml" in robots
    assert client.get("/site.css").headers["content-type"].startswith("text/css")


def test_shared_links_get_a_preview_card_in_their_language(client):
    import re

    from mikiwame.web.guides import GUIDES, SITE

    pages = {"/": "es", "/en/": "en", "/privacidad": "es", "/en/privacy": "en"}
    pages |= {g.path_es: "es" for g in GUIDES} | {g.path_en: "en" for g in GUIDES}
    pages |= {"/ejemplo": "es", "/en/example": "en"}
    for path, lang in pages.items():
        page = client.get(path).text
        meta = dict(re.findall(r'<meta property="(og:[a-z:]+)" content="([^"]*)">', page))
        assert meta["og:url"] == f"{SITE}{path}", path
        assert meta["og:title"] and meta["og:description"], path
        assert meta["og:locale"] == ("en_GB" if lang == "en" else "es_ES"), path
        image = meta["og:image"].removeprefix(SITE)
        assert image == ("/og-en.png" if lang == "en" else "/og.png"), path
        r = client.get(image)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert '<meta name="twitter:card" content="summary_large_image">' in page
