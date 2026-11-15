"""Build the static archive site (docs/, served by GitHub Pages at sukimalabs.com/estimo/).

The guides and the example report come from the same sources the live site used
(web/estimo/ and src/mikiwame/example.json); the analyzer is removed and every link is moved
under /estimo/. The closing and privacy pages are written here.

    uv run python tools/build_site.py
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from pathlib import Path

from mikiwame.web import guides

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "estimo"
OUT = ROOT / "docs"
BASE = "/estimo"
SITE = "https://sukimalabs.com" + BASE
CLOSED = "15 de noviembre de 2026"
CLOSED_EN = "15 November 2026"
# When the stored data were deleted (the bot's databases). Until it is true, the pages say
# "will be deleted"; set the date here and rebuild once it is.
DELETED_ON: str | None = None
DELETE_BY = "29 de noviembre de 2026"
DELETE_BY_EN = "29 November 2026"
REPO = "https://github.com/sukima-labs/estimo"
CONTACT = "info@patriciollorens.com"

NOTICE = {
    "es": (
        "<h2>El analizador ya no funciona</h2>\n"
        f"  <p>¿Es timo? cerró el {CLOSED}. Estas guías siguen aquí. "
        f'<a href="/">Qué fue y por qué cerró</a>.</p>\n'
    ),
    "en": (
        "<h2>The analyzer is closed</h2>\n"
        f"  <p>¿Es timo? closed on {CLOSED_EN}. These guides stay online. "
        f'<a href="/en/">What it was and why it closed</a>.</p>\n'
    ),
}
FOOTER = {
    "es": (
        '<footer>\n    Un proyecto de <a href="https://sukimalabs.com/">Sukima Labs</a> · '
        f'<a href="/privacidad">Privacidad</a> · <a href="{REPO}">Código (MIT)</a>\n  </footer>\n'
    ),
    "en": (
        '<footer>\n    A <a href="https://sukimalabs.com/">Sukima Labs</a> project · '
        f'<a href="/en/privacy">Privacy</a> · <a href="{REPO}">Code (MIT)</a>\n  </footer>\n'
    ),
}


def _dir_url(path: str) -> str:
    """/guias/x -> /estimo/guias/x/ ; /site.css -> /estimo/site.css ; / -> /estimo/"""
    if path == "/":
        return BASE + "/"
    last = path.rsplit("/", 1)[-1]
    return BASE + path + ("" if "." in last else "/")


def _rewrite(page: str) -> str:
    return re.sub(r'(href|src)="(/[^"]*)"', lambda m: f'{m[1]}="{_dir_url(m[2])}"', page)


def fix(page: str, lang: str) -> str:
    page = re.sub(
        r'  <h2 id="analizar">.*?\n  <p>(?:También en Telegram|Also on Telegram).*?</p>\n',
        "  " + NOTICE[lang],
        page,
        flags=re.S,
    )
    page = re.sub(r'\s*<p class="note">.*?</p>', "", page, flags=re.S)
    page = page.replace('<script src="/app.js"></script>\n', "")
    page = re.sub(r"<footer>.*?</footer>\n", FOOTER[lang], page, flags=re.S)
    page = page.replace(guides.SITE, SITE)
    page = _rewrite(page)
    page = re.sub(
        re.escape(SITE) + r'(/[^"]*)?"',
        lambda m: SITE + _dir_url(m[1] or "/").removeprefix(BASE) + '"',
        page,
    )
    page = page.replace(
        f'<link rel="stylesheet" href="{BASE}/site.css">',
        '<link rel="stylesheet" href="https://sukimalabs.com/marca/marca.css">\n'
        f'<link rel="stylesheet" href="{BASE}/site.css">',
    )
    page = page.replace(
        '<meta name="twitter:card"',
        '<meta name="robots" content="index,follow">\n<meta name="twitter:card"',
        1,
    )
    return page


def write(path: str, page: str) -> None:
    target = OUT / path.strip("/") / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page)


def head(lang: str, title: str, description: str, path_es: str, path_en: str) -> str:
    here = path_en if lang == "en" else path_es
    og = "og-en.png" if lang == "en" else "og.png"
    t, d = html.escape(title), html.escape(description)
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<meta name="description" content="{d}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="¿Es timo? · Sukima Labs">
<meta property="og:locale" content="{"en_GB" if lang == "en" else "es_ES"}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{SITE}{_dir_url(here).removeprefix(BASE)}">
<meta property="og:image" content="{SITE}/{og}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{BASE}/favicon.svg" type="image/svg+xml">
<link rel="canonical" href="{SITE}{_dir_url(here).removeprefix(BASE)}">
<link rel="alternate" hreflang="es" href="{SITE}{_dir_url(path_es).removeprefix(BASE)}">
<link rel="alternate" hreflang="en" href="{SITE}{_dir_url(path_en).removeprefix(BASE)}">
<link rel="alternate" hreflang="x-default" href="{SITE}{_dir_url(path_es).removeprefix(BASE)}">
<link rel="stylesheet" href="https://sukimalabs.com/marca/marca.css">
<link rel="stylesheet" href="{BASE}/site.css">
</head>
<body>
<main>
"""


def tail(lang: str) -> str:
    return f"  {_rewrite(FOOTER[lang])}</main>\n</body>\n</html>\n"


def li(items: list[tuple[str, str]]) -> str:
    return "\n".join(f'    <li><a href="{BASE}{p}">{html.escape(t)}</a></li>' for p, t in items)


def guide_titles(lang: str) -> list[tuple[str, str]]:
    folder = "guides" if lang == "en" else "guias"
    out = []
    for g in guides.GUIDES:
        slug = g.slug_en if lang == "en" else g.slug_es
        title = guides._fragment(SRC / folder / f"{slug}.html")[0]
        out.append(((g.path_en if lang == "en" else g.path_es) + "/", title))
    return out


def deletion_es() -> str:
    if DELETED_ON:
        return f"Los datos del servicio se borraron el {DELETED_ON}."
    return f"Los datos del servicio se borrarán antes del {DELETE_BY} (fecha prevista)."


def deletion_en() -> str:
    if DELETED_ON:
        return f"The service's data were deleted on {DELETED_ON}."
    return f"The service's data will be deleted before {DELETE_BY_EN} (planned date)."


def closing_es() -> str:
    return (
        head(
            "es",
            "¿Es timo? cerró · Sukima Labs",
            "¿Es timo? comprobaba anuncios de alquiler antes de pagar. Cerró el 15-11-2026. Qué fue, qué midió y por qué no llegó a nadie.",
            "/",
            "/en/",
        )
        + f"""  <a class="langs" href="{BASE}/en/" hreflang="en" lang="en">English</a>
  <p class="lights">🟢 🟠 🔴</p>
  <h1>¿Es timo? cerró</h1>
  <p class="lead">Comprobaba un anuncio de alquiler <strong>antes</strong> de pagar nada. Dejó de funcionar el {CLOSED}: casi nadie llegó a usarlo.</p>

  <h2>Qué era</h2>
  <p>Recibía el anuncio y la conversación con el «propietario» (texto o capturas) y devolvía un semáforo verde, ámbar o rojo con el porqué de cada señal y qué comprobar. Un modelo leía las capturas, otro el texto, y un tercer paso intentaba tumbar el veredicto antes de darlo. Nunca decía que alguien fuera un estafador: decía que el patrón aparece en estafas conocidas.</p>

  <h2>Qué medimos</h2>
  <div class="card">
    <ul>
      <li><strong>Precisión del rojo</strong>: en 74 pasadas sobre 37 casos inventados, ningún anuncio legítimo salió en rojo y se marcó en rojo el 82,5 % de los timos. Eran casos escritos por nosotros: no demuestran nada sobre anuncios reales.</li>
      <li><strong>Coste</strong>: unos 0,0004 € por informe (el objetivo era menos de 0,05 €).</li>
      <li><strong>Uso real</strong>: dos análisis en producción en seis semanas, uno por Telegram y uno en la web el día que abrió. Nadie contestó al seguimiento.</li>
    </ul>
  </div>

  <h2>Por qué cerró</h2>
  <p>Es una herramienta de un momento concreto, justo antes de pagar, y los canales que probamos no llegan a ese momento: grupos de vivienda (en tres de cuatro nos silenciaron), correos a quien atiende a quien busca piso, un canal propio y las búsquedas en Google, que trajeron más lectores de guías que usuarios. Preferimos cerrarlo antes que mantener un servicio que nadie usa.</p>

  <h2>Qué queda</h2>
  <ul>
{li(guide_titles("es"))}
    <li><a href="{BASE}/ejemplo/">Un ejemplo de informe</a>, con un caso inventado</li>
    <li><a href="{REPO}">El código, con licencia MIT</a></li>
    <li><a href="{REPO}/blob/main/POSTMORTEM.md">El post-mortem</a>: qué se construyó, cuánto costaba y por qué no llegó a nadie</li>
  </ul>

  <h2>Tus datos</h2>
  <p>{deletion_es()} Más en la <a href="{BASE}/privacidad/">política de privacidad</a>.</p>
"""
        + tail("es")
    )


def closing_en() -> str:
    return (
        head(
            "en",
            "¿Es timo? closed · Sukima Labs",
            "¿Es timo? checked rental ads before you paid. It closed on 15 Nov 2026. What it was, what we measured and why it reached nobody.",
            "/",
            "/en/",
        )
        + f"""  <a class="langs" href="{BASE}/" hreflang="es" lang="es">Español</a>
  <p class="lights">🟢 🟠 🔴</p>
  <h1>¿Es timo? closed</h1>
  <p class="lead">It checked a rental listing <strong>before</strong> you paid anything. It stopped on {CLOSED_EN}: almost nobody ended up using it.</p>

  <h2>What it was</h2>
  <p>It took the listing and the chat with the «landlord» (text or screenshots) and returned a green, amber or red light, the reason for each warning sign and what to check. One model read the screenshots, another the text, and a third step tried to overturn the verdict before it was given. It never said anyone was a scammer: it said the pattern appears in known scams.</p>

  <h2>What we measured</h2>
  <div class="card">
    <ul>
      <li><strong>Precision of red</strong>: in 74 runs over 37 invented cases, no legitimate ad came out red and 82.5 % of the scams were marked red. We wrote those cases ourselves: they say nothing about real ads.</li>
      <li><strong>Cost</strong>: about €0.0004 per report (the target was under €0.05).</li>
      <li><strong>Real use</strong>: two analyses in production in six weeks, one on Telegram and one on the web on opening day. Nobody answered the follow-up.</li>
    </ul>
  </div>

  <h2>Why it closed</h2>
  <p>It is a tool for one moment, right before paying, and the channels we tried do not reach that moment: housing groups (we were muted in three of four), emails to people who help flat-hunters, a channel of our own, and Google searches, which brought more guide readers than users. We chose to close it rather than keep a service nobody uses.</p>

  <h2>What stays</h2>
  <ul>
{li(guide_titles("en"))}
    <li><a href="{BASE}/en/example/">An example report</a>, with an invented case</li>
    <li><a href="{REPO}">The code, MIT licensed</a></li>
    <li><a href="{REPO}/blob/main/POSTMORTEM.md">The post-mortem</a> (in Spanish): what was built, what it cost and why it reached nobody</li>
  </ul>

  <h2>Your data</h2>
  <p>{deletion_en()} More in the <a href="{BASE}/en/privacy/">privacy policy</a>.</p>
"""
        + tail("en")
    )


def privacy_es() -> str:
    return (
        head(
            "es",
            "Privacidad · ¿Es timo?",
            "Qué datos trató «¿Es timo?», qué pasó con ellos al cerrar y a quién escribir.",
            "/privacidad",
            "/en/privacy",
        )
        + f"""  <a class="langs" href="{BASE}/en/privacy/" hreflang="en" lang="en">English</a>
  <p><a href="{BASE}/">← ¿Es timo?</a></p>
  <h1>Política de privacidad</h1>
  <p class="lead">Última actualización: {DELETED_ON or CLOSED}.</p>

  <h2>Este sitio</h2>
  <p>Es estático: no tiene formularios, no usa cookies ni analítica y no guarda nada de quien lo visita. Lo sirve GitHub Pages, que puede registrar direcciones IP según <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">su propia política</a>.</p>

  <h2>El servicio que cerró</h2>
  <p>Mientras funcionó (bot de Telegram desde el 1 de octubre de 2026 y web desde el 14 de octubre), trató el anuncio y la conversación que cada persona envió, con un modelo de IA de OpenAI al que se accedía a través de OpenCode, y guardó huellas irreversibles (HMAC) de teléfonos, IBAN, correos y nombres de anunciantes para reconocer anuncios repetidos. Dejó de funcionar el {CLOSED}.</p>
  <p><strong>{deletion_es()}</strong> Se borra todo: textos e informes, huellas, huellas de fotos, la copia de anuncios usada para comparar precios, los registros de uso y las fichas anónimas.</p>

  <h2>Responsable y tus derechos</h2>
  <p>Patricio Llorens. Para acceso, rectificación, supresión, oposición o limitación, o si eres propietario y crees que tus datos aparecían por error: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""
        + tail("es")
    )


def privacy_en() -> str:
    return (
        head(
            "en",
            "Privacy · ¿Es timo?",
            "What data ¿Es timo? processed, what happened to it when it closed and who to write to.",
            "/privacidad",
            "/en/privacy",
        )
        + f"""  <a class="langs" href="{BASE}/privacidad/" hreflang="es" lang="es">Español</a>
  <p><a href="{BASE}/en/">← ¿Es timo?</a></p>
  <h1>Privacy policy</h1>
  <p class="lead">Last updated: {DELETED_ON or CLOSED_EN}.</p>

  <h2>This site</h2>
  <p>It is static: no forms, no cookies, no analytics, and it stores nothing about visitors. It is served by GitHub Pages, which may log IP addresses under <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">its own policy</a>.</p>

  <h2>The service that closed</h2>
  <p>While it ran (a Telegram bot from 1 October 2026 and a website from 14 October), it processed the listing and the conversation each person sent, with an OpenAI AI model reached through OpenCode, and kept irreversible fingerprints (HMAC) of advertisers' phones, IBANs, emails and names to recognise repeated ads. It stopped on {CLOSED_EN}.</p>
  <p><strong>{deletion_en()}</strong> Everything goes: texts and reports, fingerprints, photo fingerprints, the listings copy used for price comparison, usage logs and the anonymous case sheets.</p>

  <h2>Controller and your rights</h2>
  <p>Patricio Llorens. For access, rectification, erasure, objection or restriction, or if you are a landlord who thinks your data appeared by mistake: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""
        + tail("en")
    )


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / ".nojekyll").write_text("")
    for name in ("site.css", "favicon.svg", "og.png", "og-en.png"):
        shutil.copy(SRC / name, OUT / name)
    for path, page in guides.build_pages(SRC).items():
        write(path, fix(page.decode(), "en" if path.startswith("/en/") else "es"))
    from mikiwame.example import example as saved_example
    from mikiwame.web.app import safe_report_html

    ex = {}
    for lang in ("es", "en"):
        case, level, report = saved_example(lang)
        ex[lang] = (case, level, safe_report_html(report))
    for path, page in guides.example_pages(SRC, ex).items():
        write(path, fix(page.decode(), "en" if path.startswith("/en/") else "es"))
    write("/", closing_es())
    write("/en/", closing_en())
    write("/privacidad", privacy_es())
    write("/en/privacy", privacy_en())
    if "--sitemap" in sys.argv:  # lines for sukima-labs.github.io/sitemap.xml
        for p in sorted(OUT.rglob("index.html")):
            rel = p.parent.relative_to(OUT).as_posix()
            print(f"  <url><loc>{SITE}/{'' if rel == '.' else rel + '/'}</loc></url>")


if __name__ == "__main__":
    main()
