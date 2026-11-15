"""The website: the same analysis as the bot, for people who do not use Telegram.

Decided 14-10-2026 (Patricio): «tenemos que encontrar usuarios». It runs next to the bot on the
production database, so website cases and bot cases meet in the registry. Behind a reverse
proxy that terminates TLS; here it is plain HTTP.

Who is who: a visitor is a negative id from an HMAC of their IP. They are never a member: all of
them are one branch of the trust tree (store.WEB_BRANCH), and nobody gets the follow-up
question. Screenshots arrive already shrunk by the browser, as base64 inside a JSON body read
into memory, so no image ever touches the disk (a multipart upload spools to a temp file).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from mikiwame import PROJECT_DIR
from mikiwame.config import Settings
from mikiwame.example import example as saved_example
from mikiwame.extract.build import Draft, DraftItem
from mikiwame.limits import Limiter
from mikiwame.pipeline import AnalysisFailed, Analyzer
from mikiwame.store.sql import Store
from mikiwame.web.guides import EXAMPLE_PATHS, ROBOTS, build_pages, example_pages, sitemap
from mikiwame.web.images import BadImage, normalize

log = logging.getLogger(__name__)

PAGES_DIR = PROJECT_DIR / "web" / "estimo"
# Path → file under PAGES_DIR. Nothing else is served: no directory listing, no guessing.
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/privacidad": ("privacidad.html", "text/html; charset=utf-8"),
    "/en/": ("en.html", "text/html; charset=utf-8"),
    "/en": ("en.html", "text/html; charset=utf-8"),
    "/en/privacy": ("privacy.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/site.css": ("site.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    # Link previews (Open Graph): evaluation/og.py draws them.
    "/og.png": ("og.png", "image/png"),
    "/og-en.png": ("og-en.png", "image/png"),
    # Google Search Console ownership (16-10-2026, Patricio's account): keep it, or the site
    # stops being verified.
    "/google73830429acde5d27.html": ("google73830429acde5d27.html", "text/html; charset=utf-8"),
}
SECURITY_HEADERS = {
    # Reports quote hostile text: even though it is escaped, no script may run but ours.
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
# As long as the bot keeps a previous case for the advertiser's answer.
CONTINUE_TTL = dt.timedelta(hours=1)
MAX_PENDING = 500
MAX_TEXT_FIELD = 20_000

ERRORS = {
    "too_large": (
        "Es demasiado grande: manda menos capturas o más pequeñas.",
        "It is too large: send fewer or smaller screenshots.",
    ),
    "bad_request": ("Petición no válida.", "Invalid request."),
    "expired": (
        "Ese caso ya no está abierto (pasa una hora). Empieza uno nuevo.",
        "That case is no longer open (it lasts an hour). Start a new one.",
    ),
    "too_many": (
        "Como mucho {n} capturas por caso, contando las de antes.",
        "At most {n} screenshots per case, counting the earlier ones.",
    ),
    "image_too_large": (
        "Una de las capturas es demasiado grande.",
        "One of the screenshots is too large.",
    ),
    "unreadable": (
        "Una de las imágenes no se puede leer: mándala como PNG o JPG.",
        "One of the images cannot be read: send it as PNG or JPG.",
    ),
    "empty": (
        "Pega el anuncio o la conversación, o añade capturas, y vuelve a pulsar.",
        "Paste the listing or the conversation, or add screenshots, and press again.",
    ),
    "busy": (
        "Ya tienes un análisis en marcha: espera a que termine.",
        "You already have an analysis running: wait for it to finish.",
    ),
    "crash": (
        "Algo ha fallado por nuestra parte. Inténtalo de nuevo en un rato.",
        "Something went wrong on our side. Please try again in a while.",
    ),
}

# The report is Telegram HTML built by report.py, which escapes everything it interpolates.
# Belt and braces: only these tags survive; any other "<" is shown as text.
_ALLOWED_TAG = re.compile(r"</?(?:b|i|code)>")


def safe_report_html(report: str) -> str:
    out, last = [], 0
    for m in _ALLOWED_TAG.finditer(report):
        out.append(report[last : m.start()].replace("<", "&lt;").replace(">", "&gt;"))
        out.append(m.group(0))
        last = m.end()
    out.append(report[last:].replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(out)


@dataclass
class Pending:
    """A finished case the visitor may add the advertiser's answer to."""

    draft: Draft
    at: dt.datetime


class WebApp:
    def __init__(self, settings: Settings, store: Store, analyzer: Analyzer) -> None:
        self.settings = settings
        self.store = store
        self.analyzer = analyzer
        self.limiter = Limiter(settings, store)
        self.slots = asyncio.Semaphore(settings.web_concurrency)
        self.busy: set[int] = set()
        self.pending: dict[str, Pending] = {}
        self._key = settings.hmac_key.get_secret_value().encode()
        self.guides = build_pages(PAGES_DIR)
        examples = {}
        for lang in ("es", "en"):
            case, level, report = saved_example(lang)
            examples[lang] = (case, level, safe_report_html(report))
        self.guides |= example_pages(PAGES_DIR, examples)
        self.sitemap = sitemap()

    # --- Identity -------------------------------------------------------------------------

    def client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else ""
        if peer in self.settings.web_trusted_proxies:
            # The proxy appends the address it saw: the last hop is the one we can trust.
            fwd = request.headers.get("x-forwarded-for", "")
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            if hops:
                return hops[-1]
        return peer

    def visitor_id(self, ip: str) -> int:
        digest = hmac.new(self._key, b"web-ip:" + ip.encode(), hashlib.sha256).digest()
        return -(int.from_bytes(digest[:6], "big") or 1)

    @staticmethod
    def event(vid: int, what: str) -> None:
        """Same idea as the bot's `user <id>:` lines: what happened, never the content."""
        log.info("web %x: %s", -vid, what)

    # --- Routes ---------------------------------------------------------------------------

    async def page(self, request: Request) -> Response:
        name, media = PAGES[request.url.path]
        path = PAGES_DIR / name
        if not path.is_file():
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(path, media_type=media, headers=SECURITY_HEADERS)

    async def guide(self, request: Request) -> Response:
        if request.url.path in EXAMPLE_PATHS.values():
            self.event(self.visitor_id(self.client_ip(request)), "example page shown")
        return Response(
            self.guides[request.url.path],
            media_type="text/html; charset=utf-8",
            headers=SECURITY_HEADERS,
        )

    async def sitemap_xml(self, _: Request) -> Response:
        return Response(self.sitemap, media_type="application/xml")

    async def robots(self, _: Request) -> Response:
        return Response(ROBOTS, media_type="text/plain; charset=utf-8")

    async def healthz(self, _: Request) -> Response:
        return PlainTextResponse("ok")

    async def example(self, request: Request) -> Response:
        """The invented case and its saved report: nothing charged, no model, nothing stored."""
        lang = "en" if request.query_params.get("lang") == "en" else "es"
        case, level, report = saved_example(lang)
        self.event(self.visitor_id(self.client_ip(request)), "example shown")
        return JSONResponse(
            {"ok": True, "case": case, "level": level, "report": safe_report_html(report)},
            headers=SECURITY_HEADERS,
        )

    def _fail(self, status: int, message: str) -> JSONResponse:
        return JSONResponse({"ok": False, "error": message}, status, headers=SECURITY_HEADERS)

    def _error(self, status: int, code: str, lang: str, **fmt) -> JSONResponse:
        es, en = ERRORS[code]
        return self._fail(status, (en if lang == "en" else es).format(**fmt))

    async def _body(self, request: Request, limit: int) -> bytes | None:
        size = request.headers.get("content-length")
        if size is not None and size.isdigit() and int(size) > limit:
            return None
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def _prune(self, now: dt.datetime) -> None:
        for token in [t for t, p in self.pending.items() if now - p.at > CONTINUE_TTL]:
            self.pending.pop(token, None)
        while len(self.pending) > MAX_PENDING:
            self.pending.pop(next(iter(self.pending)))

    async def analyze(self, request: Request) -> Response:
        vid = self.visitor_id(self.client_ip(request))
        max_image = self.settings.web_max_image_mb * 1024 * 1024
        # base64 is 4/3 of the bytes, plus the text and some slack.
        limit = self.settings.max_images * max_image * 4 // 3 + 256 * 1024
        # Before the body is read, the page's language comes from the query string.
        lang = "en" if request.query_params.get("lang") == "en" else "es"
        try:
            raw = await self._body(request, limit)
        except ClientDisconnect:
            # The visitor closed the page while uploading: nobody is left to answer.
            self.event(vid, "left before the upload finished")
            return Response(status_code=400)
        if raw is None:
            self.event(vid, "request too large")
            return self._error(413, "too_large", lang)
        try:
            body = json.loads(raw)
            text = str(body.get("text") or "")[:MAX_TEXT_FIELD]
            images_b64 = body.get("images") or []
            token = body.get("continue") or None
            if not isinstance(images_b64, list) or not all(isinstance(i, str) for i in images_b64):
                raise ValueError("images")
        except (ValueError, AttributeError):
            return self._error(400, "bad_request", lang)

        now = dt.datetime.now(dt.UTC)
        self._prune(now)
        draft = Draft(user_id=vid)
        if token:
            prev = self.pending.get(str(token))
            if prev is None:
                return self._error(410, "expired", lang)
            draft.items = list(prev.draft.items)
            draft.continues = True
        if len(draft.images()) + len(images_b64) > self.settings.max_images:
            return self._error(400, "too_many", lang, n=self.settings.max_images)
        try:
            for b64 in images_b64:
                data = base64.b64decode(b64, validate=True)
                if len(data) > max_image:
                    return self._error(413, "image_too_large", lang)
                draft.items.append(DraftItem(kind="image", image=normalize(data)))
        except binascii.Error:
            return self._error(400, "bad_request", lang)
        except BadImage as e:
            return self._error(400, e.code, lang)
        if text.strip():
            draft.items.append(DraftItem(kind="text", text=text))
        if not draft.items or (draft.continues and not images_b64 and not text.strip()):
            return self._error(400, "empty", lang)

        notice = self.analyzer.unknown_link_only(draft, lang)
        if notice:
            self.event(vid, f"draft {draft.case_id}: unknown-link notice, not charged")
            return JSONResponse(
                {"ok": True, "level": None, "report": safe_report_html(notice)},
                headers=SECURITY_HEADERS,
            )
        if vid in self.busy:
            return self._error(429, "busy", lang)
        decision = self.limiter.check_web(vid, lang)
        if not decision.allowed:
            self.event(vid, f"limit reached: {decision.code}")
            return self._fail(429, decision.reason or "")

        self.busy.add(vid)
        try:
            self.store.record_usage(vid)
            self.event(vid, f"draft {draft.case_id}: analysis charged and started")
            async with self.slots:
                try:
                    result = await self.analyzer.analyze_draft(draft, lang)
                except AnalysisFailed as e:
                    self.event(vid, f"case {draft.case_id}: analysis failed (notice sent)")
                    return self._fail(503, str(e))
                except Exception:
                    log.exception("web analysis crashed for case %s", draft.case_id)
                    self.event(vid, f"case {draft.case_id}: analysis crashed (error sent)")
                    return self._error(500, "crash", lang)
        finally:
            self.busy.discard(vid)
        new_token = secrets.token_urlsafe(18)
        self.pending[new_token] = Pending(draft, dt.datetime.now(dt.UTC))
        if token:
            self.pending.pop(str(token), None)
        level = result.verdict.level.key
        self.event(vid, f"case {draft.case_id}: {level} report, delivered")
        return JSONResponse(
            {
                "ok": True,
                "level": level,
                "report": safe_report_html(result.report),
                "continue": new_token,
            },
            headers=SECURITY_HEADERS,
        )

    def build(self) -> Starlette:
        routes = [Route(path, self.page, methods=["GET", "HEAD"]) for path in PAGES]
        routes += [Route(path, self.guide, methods=["GET", "HEAD"]) for path in self.guides]
        routes += [
            Route("/sitemap.xml", self.sitemap_xml, methods=["GET", "HEAD"]),
            Route("/robots.txt", self.robots, methods=["GET", "HEAD"]),
        ]
        routes += [
            Route("/healthz", self.healthz, methods=["GET"]),
            Route("/api/analyze", self.analyze, methods=["POST"]),
            Route("/api/example", self.example, methods=["GET"]),
        ]
        return Starlette(routes=routes)


def run(settings: Settings) -> None:
    import uvicorn

    from mikiwame.llm.client import LLMClient
    from mikiwame.llm.router import LLMRouter
    from mikiwame.store.market import MarketStore

    store = Store(settings.database_url)
    client = LLMClient(timeout_s=settings.llm_timeout_s, env=settings.provider_env())
    router = LLMRouter(settings, store, client)
    market = MarketStore(settings.market_database_url)
    # No street check here: Nominatim allows 1 request/s in all, and the bot already uses it
    # from this same address. The check is informative only (weight 0): no verdict changes.
    web = WebApp(settings, store, Analyzer(settings, store, router, market, None))
    # No access log: it would put every visitor's IP in the journal. web.event() logs instead.
    uvicorn.run(
        web.build(),
        host=settings.web_host,
        port=settings.web_port,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        log_level="info",
    )
