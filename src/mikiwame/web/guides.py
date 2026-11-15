"""Guides: short pages on the usual rental scams, in Spanish and English, each with the analyzer.

Someone who suspects a flat searches the web, not Telegram (16-10-2026: two weeks of outreach in
groups brought two people and no analysis). Each guide is an HTML fragment in web/estimo/guias/
or web/estimo/guides/ that starts with its title and description in comments; the page around it
(head, the analyzer block taken from the home page, links to the other guides) is built here,
once, at start-up. Also the sitemap.xml and robots.txt for search engines.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

SITE = "https://estimo.patriciollorens.com"


@dataclass(frozen=True)
class Guide:
    slug_es: str
    slug_en: str

    @property
    def path_es(self) -> str:
        return f"/guias/{self.slug_es}"

    @property
    def path_en(self) -> str:
        return f"/en/guides/{self.slug_en}"


# Order = order of the "other guides" lists and of the sitemap.
GUIDES = [
    Guide("propietario-en-el-extranjero", "landlord-abroad"),
    Guide("reserva-antes-de-ver-el-piso", "deposit-before-viewing"),
    Guide("comprobar-propietario", "check-the-landlord"),
    Guide("ya-he-pagado", "already-paid"),
    Guide("gastos-de-gestion", "agency-fees"),
    Guide("te-piden-el-dni", "asked-for-documents"),
    Guide("habitacion-de-estudiante", "student-room"),
]

# The shareable example report (a link someone can send on WhatsApp shows a card, 27-10-2026).
EXAMPLE = Guide("ejemplo", "example")
EXAMPLE_PATHS = {"es": "/ejemplo", "en": "/en/example"}

# The other pages search engines should know, as (Spanish, English) pairs.
PAIRS = [("/", "/en/"), ("/privacidad", "/en/privacy"), tuple(EXAMPLE_PATHS.values())]

WORDS = {
    "es": {
        "home": "¿Es timo?",
        "og_locale": "es_ES",
        "og_image": "og.png",
        "other_lang": ("English", "en"),
        "check": "Compruébalo con tu caso",
        "check_lead": "Pega el anuncio y la conversación, o sube capturas. Es gratis y no "
        "hace falta registrarse.",
        "others": "Otras guías",
        "telegram": 'También en Telegram: <a href="https://t.me/EsTimoBot">@EsTimoBot</a>.',
        "footer": 'Responsable y contacto: Patricio Llorens · '
        '<a href="mailto:info@patriciollorens.com">info@patriciollorens.com</a> · '
        '<a href="/privacidad">Privacidad</a>',
        "example_title": "Ejemplo de informe: ¿es timo este piso?",
        "example_description": "Un caso inventado del «propietario en el extranjero» y el "
        "informe que devuelve ¿Es timo?: semáforo, señales y qué comprobar antes de pagar. Gratis.",
        "example_body": """    <h1>Así es un informe de ¿Es timo?</h1>
    <p class="lead">Un caso inventado (los datos no son de nadie) y el informe real que
    devolvió la herramienta.</p>

    <h2>Lo que pega la persona</h2>
    <div class="card example-case">{case}</div>

    <h2>Lo que le devolvemos</h2>
    <div class="card result {level}">{report}</div>""",
    },
    "en": {
        "home": "¿Es timo? · Is it a scam?",
        "og_locale": "en_GB",
        "og_image": "og-en.png",
        "other_lang": ("Español", "es"),
        "check": "Check your own case",
        "check_lead": "Paste the listing and the conversation, or upload screenshots. It is "
        "free and there is nothing to sign up for.",
        "others": "Other guides",
        "telegram": 'Also on Telegram: <a href="https://t.me/EsTimoBot">@EsTimoBot</a>.',
        "footer": 'Controller and contact: Patricio Llorens · '
        '<a href="mailto:info@patriciollorens.com">info@patriciollorens.com</a> · '
        '<a href="/en/privacy">Privacy</a>',
        "example_title": "Example report: is this flat a scam?",
        "example_description": "A made-up «landlord abroad» case and the report ¿Es timo? "
        "returns: traffic light, warning signs and what to check before paying. Free.",
        "example_body": """    <h1>What a ¿Es timo? report looks like</h1>
    <p class="lead">A made-up case (the data belong to nobody) and the real report the tool
    returned.</p>

    <h2>What the person pastes</h2>
    <div class="card example-case">{case}</div>

    <h2>What we send back</h2>
    <div class="card result {level}">{report}</div>""",
    },
}

_META = re.compile(r"<!--\s*(title|description):\s*(.*?)\s*-->\n?", re.S)
_ANALYZER = re.compile(r"  <!-- analyzer:.*?-->\n(.*?)  <!-- /analyzer -->\n", re.S)

LAYOUT = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="¿Es timo?">
<meta property="og:locale" content="{og_locale}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{site}{path}">
<meta property="og:image" content="{site}/{og_image}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="canonical" href="{site}{path}">
<link rel="alternate" hreflang="es" href="{site}{path_es}">
<link rel="alternate" hreflang="en" href="{site}{path_en}">
<link rel="alternate" hreflang="x-default" href="{site}{path_es}">
<link rel="stylesheet" href="/site.css">
</head>
<body>
<main>
  <a class="langs" href="{other_path}" hreflang="{other_code}" lang="{other_code}">{other_name}</a>
  <p class="brand"><a href="{home}">🟢 🟠 🔴 {home_name}</a></p>
  <article class="guide">
{body}
  </article>

  <h2 id="analizar">{check}</h2>
  <p>{check_lead}</p>
{analyzer}
  <p>{telegram}</p>

  <h2>{others}</h2>
  <ul>
{others_list}
  </ul>

  <footer>
    {footer}
  </footer>
</main>
<script src="/app.js"></script>
</body>
</html>
"""


def _fragment(path: Path) -> tuple[str, str, str]:
    """(title, description, body) of a guide fragment."""
    text = path.read_text()
    meta = dict(_META.findall(text))
    if "title" not in meta or "description" not in meta:
        raise ValueError(f"{path}: needs <!-- title: … --> and <!-- description: … -->")
    return meta["title"], meta["description"], _META.sub("", text).strip()


def _analyzer(home_page: Path) -> str:
    m = _ANALYZER.search(home_page.read_text())
    if m is None:
        raise ValueError(f"{home_page}: no <!-- analyzer --> block")
    return m.group(1).rstrip("\n")


def build_pages(pages_dir: Path) -> dict[str, bytes]:
    """Every guide page, by path, in both languages."""
    analyzer = {"es": _analyzer(pages_dir / "index.html"), "en": _analyzer(pages_dir / "en.html")}
    titles: dict[tuple[str, Guide], str] = {}
    parts = {}
    for g in GUIDES:
        for lang, folder, slug in (("es", "guias", g.slug_es), ("en", "guides", g.slug_en)):
            title, description, body = _fragment(pages_dir / folder / f"{slug}.html")
            titles[lang, g] = title
            parts[lang, g] = (title, description, body)
    out = {}
    for (lang, g), (title, description, body) in parts.items():
        out[g.path_en if lang == "en" else g.path_es] = _render(
            lang, g, title, description, body, analyzer[lang], titles, [o for o in GUIDES if o != g]
        )
    return out


def example_pages(pages_dir: Path, examples: dict[str, tuple[str, str, str]]) -> dict[str, bytes]:
    """The example report as a page of its own, by path: lang → (case, level, safe report HTML)."""
    analyzer = {"es": _analyzer(pages_dir / "index.html"), "en": _analyzer(pages_dir / "en.html")}
    titles = {}
    for g in GUIDES:
        for lang, folder, slug in (("es", "guias", g.slug_es), ("en", "guides", g.slug_en)):
            titles[lang, g] = _fragment(pages_dir / folder / f"{slug}.html")[0]
    out = {}
    for lang, (case, level, report) in examples.items():
        w = WORDS[lang]
        body = w["example_body"].format(case=html.escape(case), level=level, report=report)
        out[EXAMPLE_PATHS[lang]] = _render(
            lang,
            EXAMPLE,
            w["example_title"],
            w["example_description"],
            body,
            analyzer[lang],
            titles,
            GUIDES,
        )
    return out


def _render(
    lang: str,
    g: Guide,
    title: str,
    description: str,
    body: str,
    analyzer: str,
    titles: dict[tuple[str, Guide], str],
    others: list[Guide],
) -> bytes:
    w = WORDS[lang]
    path_es, path_en = (
        (EXAMPLE_PATHS["es"], EXAMPLE_PATHS["en"]) if g is EXAMPLE else (g.path_es, g.path_en)
    )
    path = path_en if lang == "en" else path_es
    other_name, other_code = w["other_lang"]
    others_list = "\n".join(
        f'    <li><a href="{o.path_en if lang == "en" else o.path_es}">'
        f"{html.escape(titles[lang, o])}</a></li>"
        for o in others
    )
    page = LAYOUT.format(
        lang=lang,
        title=html.escape(f"{title} · ¿Es timo?"),
        description=html.escape(description),
        site=SITE,
        og_locale=w["og_locale"],
        og_image=w["og_image"],
        path=path,
        path_es=path_es,
        path_en=path_en,
        other_path=path_es if lang == "en" else path_en,
        other_code=other_code,
        other_name=other_name,
        home="/en/" if lang == "en" else "/",
        home_name=w["home"],
        body=body,
        check=w["check"],
        check_lead=w["check_lead"],
        analyzer=analyzer,
        telegram=w["telegram"],
        others=w["others"],
        others_list=others_list,
        footer=w["footer"],
    )
    return page.encode()


def sitemap() -> bytes:
    """Every public page, each with its other-language twin (hreflang)."""
    pairs = PAIRS + [(g.path_es, g.path_en) for g in GUIDES]
    urls = []
    for es, en in pairs:
        alternates = (
            f'    <xhtml:link rel="alternate" hreflang="es" href="{SITE}{es}"/>\n'
            f'    <xhtml:link rel="alternate" hreflang="en" href="{SITE}{en}"/>\n'
        )
        for loc in (es, en):
            urls.append(f"  <url>\n    <loc>{SITE}{loc}</loc>\n{alternates}  </url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(urls) + "\n</urlset>\n"
    ).encode()


ROBOTS = f"User-agent: *\nDisallow: /api/\nSitemap: {SITE}/sitemap.xml\n".encode()
