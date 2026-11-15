"""Telegram bot: collects a case (texts + screenshots), runs the analysis, replies with a report.

Only this process may poll @botipibot6_bot: a second getUpdates caller gets 409 and both go
mute. Repeated Conflicts from polling alert the admin; a lone one is a poll left stranded by a
change of network path (the host's uplink on 2026-10-06) and clears by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import html
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Conflict, NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from mikiwame import PROJECT_DIR
from mikiwame.bot import texts, texts_en
from mikiwame.bot.alert import send_admin
from mikiwame.bot.sdnotify import notify, watchdog_interval_s
from mikiwame.config import Settings
from mikiwame.example import example
from mikiwame.extract import text as tx
from mikiwame.extract.build import Draft, DraftItem
from mikiwame.geo import StreetChecker
from mikiwame.limits import Limiter
from mikiwame.llm.client import LLMClient
from mikiwame.llm.router import LLMRouter
from mikiwame.pipeline import AnalysisFailed, Analyzer
from mikiwame.store.market import MarketStore
from mikiwame.store.sql import PUBLIC, Store

log = logging.getLogger(__name__)

DRAFT_TTL = dt.timedelta(hours=1)
# After a report, what the user sends next (typically the advertiser's answer to a suggested
# question) joins the same case for this long, so both are analysed together.
FOLLOW_UP_TTL = dt.timedelta(hours=1)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
STATE_DIR = PROJECT_DIR / "var" / "state"
# A real second poller keeps getting 409 on every retry; a poll stranded on an old network path
# gets one or two until Telegram drops it. Alert from the third within the window.
CONFLICT_WINDOW = dt.timedelta(minutes=5)
CONFLICTS_TO_ALERT = 3
# Telegram app languages that get Spanish; any other gets English (14-10-2026). No language
# code at all: Spanish, the bot's own language.
SPANISH_APPS = ("es", "ca", "gl", "eu")


def app_lang(code: str | None) -> str:
    if not code or code.split("-")[0].lower() in SPANISH_APPS:
        return "es"
    return "en"


class MikiwameBot:
    def __init__(self, settings: Settings, store: Store, analyzer: Analyzer) -> None:
        self.settings = settings
        self.store = store
        self.analyzer = analyzer
        self.limiter = Limiter(settings, store)
        self.drafts: dict[int, Draft] = {}
        # Last analysed draft per user, with when it was analysed (images stay in memory only).
        self.previous: dict[int, tuple[Draft, dt.datetime]] = {}
        self.status_msg: dict[int, Message] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.busy: set[int] = set()
        self._closed_replied: dict[int, dt.datetime] = {}
        self._tasks: list[asyncio.Task] = []
        self._conflicts: list[dt.datetime] = []
        self.marker = STATE_DIR / f"running-{settings.instance}"
        self.label = f"Mikiwame ({settings.instance})"
        # Language: what the user chose with /english or /espanol (cached from the database),
        # else their Telegram app's, seen on each update.
        self.chosen_lang: dict[int, str | None] = {}
        self.app_lang: dict[int, str] = {}

    # --- helpers ---------------------------------------------------------------------------

    def lang(self, uid: int) -> str:
        if uid not in self.chosen_lang:
            self.chosen_lang[uid] = self.store.chosen_lang(uid)
        return self.chosen_lang[uid] or self.app_lang.get(uid, "es")

    def t(self, uid: int):
        """The texts module in the user's language."""
        return texts_en if self.lang(uid) == "en" else texts

    def public_text(self, uid: int, name: str, **kw) -> str:
        """What public members get: the daily limit only, or a quota in total (`<name>_QUOTA`)."""
        quota = self.settings.public_quota
        if quota is None:
            return getattr(self.t(uid), name).format(daily=self.settings.limit_user_daily, **kw)
        return getattr(self.t(uid), f"{name}_QUOTA").format(n=quota, **kw)

    def see_app_lang(self, update: Update) -> None:
        user = update.effective_user
        if user is not None:
            self.app_lang[user.id] = app_lang(getattr(user, "language_code", None))

    def alert(self, text: str, key: str | None = None) -> None:
        """Fire-and-forget admin alert, off the event loop (it is a blocking HTTP call)."""
        args = (
            self.settings.telegram_bot_token.get_secret_value(),
            self.settings.admin_chat_id,
            text,
        )
        task = asyncio.get_running_loop().run_in_executor(
            None, lambda: send_admin(*args, key=key, label=self.label)
        )
        self._tasks.append(task)  # keep a reference until shutdown

    @staticmethod
    def event(uid: int, what: str) -> None:
        """What the bot did for a user, never what was said: no message text, no report, no
        identifiers from the case. Lets a reply be checked without opening the chat (Patricio,
        05-10-2026: «no podemos depender de una sesión abierta»)."""
        log.info("user %s: %s", uid, what)

    def lock(self, uid: int) -> asyncio.Lock:
        return self.locks.setdefault(uid, asyncio.Lock())

    def draft(self, uid: int) -> Draft:
        d = self.drafts.get(uid)
        now = dt.datetime.now(dt.UTC)
        if d is None or now - d.created_at > DRAFT_TTL:
            d = Draft(user_id=uid)
            prev = self.previous.get(uid)
            if prev and now - prev[1] <= FOLLOW_UP_TTL:
                d.items = list(prev[0].items)
                d.continues = True
            self.drafts[uid] = d
            self.status_msg.pop(uid, None)
        return d

    def reset(self, uid: int, *, forget: bool = True) -> None:
        """Drop the draft in progress; with forget, also the previous case (no follow-up)."""
        self.drafts.pop(uid, None)
        self.status_msg.pop(uid, None)
        if forget:
            self.previous.pop(uid, None)

    async def gate(self, update: Update) -> bool:
        """Allowlist check. Strangers get one polite reply per day and cost nothing."""
        user = update.effective_user
        if user is None:
            return False
        self.see_app_lang(update)
        uid = user.id
        if self.limiter.is_allowed_user(user.id):
            return True
        if self.settings.open_access and self.store.join_public(user.id):
            log.info("public member joined: user %s", user.id)
            return True
        now = dt.datetime.now(dt.UTC)
        last = self._closed_replied.get(user.id)
        if last is None or now - last > dt.timedelta(days=1):
            self._closed_replied[user.id] = now
            log.info("not allowed: user %s (@%s)", user.id, user.username)
            if update.effective_message:
                await update.effective_message.reply_text(self.t(uid).CLOSED)
        return False

    def keyboard(self, uid: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(self.t(uid).BUTTON_ANALYZE, callback_data="analyze"),
                    InlineKeyboardButton(self.t(uid).BUTTON_RESET, callback_data="reset"),
                ]
            ]
        )

    async def show_status(self, update: Update, uid: int) -> None:
        d = self.drafts[uid]
        images, txts = d.summary()
        status = self.t(uid).DRAFT_STATUS_CONTINUE if d.continues else self.t(uid).DRAFT_STATUS
        body = status.format(what=self.t(uid).what(images, txts))
        notes = []
        if not any(tx.listing_ref(u) for t in d.texts() for u in tx.urls(t)):
            body += "\n" + self.t(uid).ASK_LINK
            notes.append("asked for the link")
        elif unknown := self.analyzer.unknown_link_only(d, self.lang(uid)):
            body += "\n\n" + unknown
            notes.append("unknown-link notice")
        self.event(
            uid,
            f"draft {d.case_id}: {images} image(s), {txts} text(s)"
            + (", continues previous case" if d.continues else "")
            + "".join(f", {n}" for n in notes),
        )
        old = self.status_msg.pop(uid, None)
        if old is not None:
            with contextlib.suppress(TelegramError):
                await old.delete()
        msg = await update.effective_message.reply_text(
            body, parse_mode=ParseMode.HTML, reply_markup=self.keyboard(uid)
        )
        self.status_msg[uid] = msg

    # --- handlers --------------------------------------------------------------------------

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Deep link t.me/<bot>?start=inv_<code>: the way in for invited people.
        payload = context.args[0] if context.args else ""
        user = update.effective_user
        self.see_app_lang(update)
        uid = user.id if user else 0
        member = self.store.member(user.id) if user else None
        # Members with an invite do not redeem another; public members can (an upgrade).
        invited = member is not None and member["campaign"] != PUBLIC
        if (
            payload.startswith("inv_")
            and user
            and not self.limiter.is_root(user.id)
            and not (invited and member["revoked_at"] is None)
        ):
            ok = self.store.redeem_invite(
                payload[4:], user.id, invites_for_new=self.settings.invites_per_member
            )
            log.info("invite %s for user %s", "redeemed" if ok else "rejected", user.id)
            if not ok:
                await update.effective_message.reply_text(self.t(uid).INVITE_INVALID)
                return
            await update.effective_message.reply_text(self.t(uid).INVITE_WELCOME)
        newcomer = user is not None and not self.limiter.is_allowed_user(user.id)
        if not await self.gate(update):
            return
        if newcomer:
            await update.effective_message.reply_text(self.public_text(uid, "PUBLIC_WELCOME"))
        log.info("start from user %s", update.effective_user.id)
        await update.effective_message.reply_text(
            self.t(uid).HELP,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(self.t(uid).BUTTON_EXAMPLE, callback_data="example")]]
            ),
        )

    async def on_example(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        await self.send_example(update)

    async def send_example(self, update: Update) -> None:
        """The invented case and its saved report: no analysis charged, no model, no draft."""
        uid = update.effective_user.id
        case, _, report = example(self.lang(uid))
        t = self.t(uid)
        message = update.effective_message
        await message.reply_text(
            t.EXAMPLE_INTRO.format(case=html.escape(case)), parse_mode=ParseMode.HTML
        )
        await message.reply_text(report, parse_mode=ParseMode.HTML)
        await message.reply_text(t.EXAMPLE_OUTRO, parse_mode=ParseMode.HTML)
        self.event(uid, "example shown")

    async def on_invite(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        root = self.limiter.is_root(uid)
        code = self.store.create_invite(
            uid,
            ttl_days=self.settings.invite_ttl_days,
            unlimited=root,
            max_members=self.settings.max_members,
            idle_days=self.settings.member_idle_days,
        )
        if code is None and self.settings.open_access:
            await update.effective_message.reply_text(
                self.public_text(uid, "INVITE_SHARE", link=f"https://t.me/{context.bot.username}"),
                disable_web_page_preview=True,
            )
            return
        if code is None:
            seats = self.store.beta_seats_taken(idle_days=self.settings.member_idle_days)
            full = seats >= self.settings.max_members
            await update.effective_message.reply_text(
                self.t(uid).BETA_FULL if full else self.t(uid).INVITE_NONE_LEFT
            )
            return
        log.info("invite created by user %s", uid)
        left = (
            "" if root else self.t(uid).INVITE_LEFT.format(n=self.store.member(uid)["invites_left"])
        )
        link = f"https://t.me/{context.bot.username}?start=inv_{code}"
        await update.effective_message.reply_text(
            self.t(uid).INVITE_CREATED.format(
                days=self.settings.invite_ttl_days, link=link, left=left
            ),
            disable_web_page_preview=True,
        )

    async def on_privacy(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        await update.effective_message.reply_text(
            self.t(uid).PRIVACY.format(days=self.settings.retention_days), parse_mode=ParseMode.HTML
        )

    async def on_new(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        self.reset(uid)
        await update.effective_message.reply_text(self.t(uid).RESET)

    async def on_delete(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(self.t(uid).BUTTON_YES, callback_data="delete_yes"),
                    InlineKeyboardButton(self.t(uid).BUTTON_NO, callback_data="delete_no"),
                ]
            ]
        )
        await update.effective_message.reply_text(self.t(uid).DELETE_CONFIRM, reply_markup=kb)

    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        async with self.lock(uid):
            d = self.draft(uid)
            text = update.effective_message.text or ""
            used = sum(len(t) for t in d.texts())
            d.items.append(
                DraftItem("text", text=text[: max(0, self.settings.max_text_chars - used)])
            )
            await self.show_status(update, uid)

    async def on_image(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        msg = update.effective_message
        if msg.photo:
            file_ref, size = msg.photo[-1], msg.photo[-1].file_size
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file_ref, size = msg.document, msg.document.file_size
        else:
            self.event(uid, "unsupported file")
            await msg.reply_text(self.t(uid).UNSUPPORTED)
            return
        if size and size > MAX_IMAGE_BYTES:
            self.event(uid, "image too large")
            await msg.reply_text(self.t(uid).UNSUPPORTED)
            return
        async with self.lock(uid):
            d = self.draft(uid)
            if len(d.images()) >= self.settings.max_images:
                self.event(uid, f"draft {d.case_id}: image over the limit, ignored")
                await msg.reply_text(self.t(uid).TOO_MANY_IMAGES.format(n=self.settings.max_images))
                return
            tg_file = await file_ref.get_file()
            data = bytes(await tg_file.download_as_bytearray())
            d.items.append(DraftItem("image", image=data))
            if msg.caption:
                d.items.append(DraftItem("text", text=msg.caption))
            await self.show_status(update, uid)

    async def on_other(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        self.event(uid, "unsupported message")
        await update.effective_message.reply_text(self.t(uid).UNSUPPORTED)

    async def on_lang(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/english, /espanol: the user's choice wins over their Telegram app's language."""
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        command = (update.effective_message.text or "").split()[0].lstrip("/").split("@")[0]
        lang = "en" if command.lower() in ("english", "en") else "es"
        self.store.choose_lang(uid, lang)
        self.chosen_lang[uid] = lang
        self.event(uid, f"language set to {lang}")
        await update.effective_message.reply_text(self.t(uid).LANG_SET)

    async def on_analyze_cmd(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.gate(update):
            return
        await self.analyze(update)

    async def on_button(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        with contextlib.suppress(TelegramError):  # stale or replayed presses
            await query.answer()
        if not await self.gate(update):
            return
        uid = update.effective_user.id
        if query.data == "analyze":
            await self.analyze(update)
        elif query.data == "example":
            await self.send_example(update)
        elif query.data == "reset":
            self.event(uid, "reset pressed")
            self.reset(uid)
            with contextlib.suppress(TelegramError):
                await query.edit_message_text(self.t(uid).RESET)
        elif query.data == "new_case":
            self.event(uid, "new case pressed")
            self.reset(uid)
            with contextlib.suppress(TelegramError):
                await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(self.t(uid).RESET)
        elif query.data and query.data.startswith("keep:"):
            case_id = query.data.partition(":")[2]
            row = self.store.get_case(case_id)
            # Only the owner of the case can donate it.
            ok = bool(row and row["tg_user_id"] == uid and self.store.keep_for_research(case_id))
            self.event(uid, f"case {case_id}: keep for research {'ok' if ok else 'refused'}")
            await query.message.reply_text(self.t(uid).KEPT if ok else self.t(uid).KEEP_FAILED)
        elif query.data and query.data.startswith("out:"):
            _, case_id, outcome = (query.data.split(":") + ["", ""])[:3]
            level = self.store.record_outcome(case_id, uid, outcome)
            if level is None:
                reply = self.t(uid).FOLLOWUP_DONE
            elif level == "red" and outcome == "legit":
                reply = self.t(uid).FOLLOWUP_THANKS_SORRY
                # The metric that rules: a real landlord marked red.
                self.alert("seguimiento: un usuario dice que un caso en ROJO era legítimo.")
            elif level == "green" and outcome == "scam":
                reply = self.t(uid).FOLLOWUP_THANKS_MISSED
                self.alert("seguimiento: un usuario dice que un caso en VERDE era un timo.")
            else:
                reply = self.t(uid).FOLLOWUP_THANKS
            log.info("follow-up: user %s case %s level %s → %s", uid, case_id, level, outcome)
            with contextlib.suppress(TelegramError):
                await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(reply)
        elif query.data == "delete_yes":
            if uid in self.busy:
                # Its result would be saved after the deletion and undo it.
                await query.message.reply_text(self.t(uid).DELETE_BUSY)
                return
            self.reset(uid)
            n = self.store.delete_user(uid)
            self.chosen_lang.pop(uid, None)
            log.info("user %s deleted %d cases", uid, n)
            await query.edit_message_text(self.t(uid).DELETED.format(n=n))
        elif query.data == "delete_no":
            self.event(uid, "deletion cancelled")
            await query.edit_message_text(self.t(uid).DELETE_CANCELLED)

    async def analyze(self, update: Update) -> None:
        uid = update.effective_user.id
        chat = update.effective_chat
        # Under the user's lock: waits for an image that is still downloading, and no two
        # presses can take the same draft (review 2026-10-01).
        async with self.lock(uid):
            if uid in self.busy:
                self.event(uid, "analyze pressed while busy")
                await chat.send_message(self.t(uid).BUSY)
                return
            draft = self.drafts.get(uid)
            if draft is None or not draft.items:
                self.event(uid, "analyze pressed with an empty draft")
                await chat.send_message(self.t(uid).EMPTY_DRAFT)
                return
            lang = self.lang(uid)
            if self.analyzer.unknown_link_only(draft, lang):
                self.event(uid, f"draft {draft.case_id}: unknown-link notice, not charged")
                await chat.send_message(self.t(uid).UNKNOWN_LINK_AGAIN, parse_mode=ParseMode.HTML)
                return
            decision = self.limiter.check(uid, lang)
            if not decision.allowed:
                self.event(uid, f"limit reached: {decision.code}")
                await chat.send_message(decision.reason)
                if decision.code in ("global_daily", "budget_total"):
                    self.alert(f"límite alcanzado: {decision.code}", key=decision.code)
                return
            self.busy.add(uid)
            self.reset(uid, forget=False)
        typing: asyncio.Task | None = None
        try:
            self.store.record_usage(uid, campaign_invites=self.settings.invites_per_member)
            self.event(uid, f"draft {draft.case_id}: analysis charged and started")
            progress = None
            with contextlib.suppress(TelegramError):
                progress = await chat.send_message(self.t(uid).ANALYZING)
            typing = asyncio.create_task(self._typing(chat.id, update.get_bot()))
            markup = None
            outcome = "report"
            try:
                result = await self.analyzer.analyze_draft(draft, lang)
                reply = result.report
                outcome = f"{result.verdict.level.key} report"
                self.previous[uid] = (draft, dt.datetime.now(dt.UTC))
                buttons = [
                    InlineKeyboardButton(self.t(uid).BUTTON_NEW_CASE, callback_data="new_case")
                ]
                if result.verdict.level.key in ("red", "amber"):
                    buttons.append(
                        InlineKeyboardButton(
                            self.t(uid).BUTTON_KEEP, callback_data=f"keep:{result.case.case_id}"
                        )
                    )
                markup = InlineKeyboardMarkup([[b] for b in buttons])
            except AnalysisFailed as e:
                reply = str(e)
                outcome = "analysis failed (notice sent)"
                self.drafts.setdefault(uid, draft)  # keep what was sent, so it can be retried
            except Exception:
                log.exception("analysis crashed for case %s", draft.case_id)
                self.alert(f"fallo inesperado en el caso {draft.case_id}", key="crash")
                reply = self.t(uid).ERROR
                outcome = "analysis crashed (error sent)"
                self.drafts.setdefault(uid, draft)
            if typing:
                typing.cancel()
            how = await self._deliver(chat, progress, reply, markup)
            self.event(uid, f"case {draft.case_id}: {outcome}, {how}")
        finally:
            if typing:
                typing.cancel()
            self.busy.discard(uid)

    @staticmethod
    async def _deliver(chat, progress, reply: str, markup) -> str:
        """Edit the progress message; else send it; else send it as plain text, so a report
        Telegram rejects as HTML still reaches the user (review 2026-10-01)."""
        if progress is not None:
            with contextlib.suppress(TelegramError):
                await progress.edit_text(reply, parse_mode=ParseMode.HTML, reply_markup=markup)
                return "delivered"
        try:
            await chat.send_message(reply, parse_mode=ParseMode.HTML, reply_markup=markup)
            return "delivered"
        except TelegramError:
            plain = html.unescape(re.sub(r"<[^>]+>", "", reply))[:4000]
            try:
                await chat.send_message(plain, reply_markup=markup)
                return "delivered as plain text"
            except TelegramError as e:
                return f"NOT delivered ({type(e).__name__})"

    @staticmethod
    async def _typing(chat_id: int, bot) -> None:
        with contextlib.suppress(asyncio.CancelledError, TelegramError):
            while True:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(4.5)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if isinstance(err, Conflict):
            now = dt.datetime.now(dt.UTC)
            self._conflicts = [t for t in self._conflicts if now - t < CONFLICT_WINDOW]
            self._conflicts.append(now)
            n = len(self._conflicts)
            log.error("409 Conflict (%d in %d min): another poller or a stranded poll", n,
                      CONFLICT_WINDOW.seconds // 60)  # fmt: skip
            if n >= CONFLICTS_TO_ALERT:
                self.alert(
                    f"{n} errores 409 en {CONFLICT_WINDOW.seconds // 60} min: otro proceso está "
                    "haciendo getUpdates con el bot. Dos pollers = bot mudo.",
                    key="conflict",
                )
            return
        if update is None and isinstance(err, NetworkError):
            # Polling hiccup (Bad Gateway, timeouts): the library retries on its own.
            log.warning("polling network error: %s", err)
            return
        log.error("unhandled error", exc_info=err)

    # --- lifecycle -------------------------------------------------------------------------

    def claim_marker(self) -> None:
        """Before polling starts. A marker left over means the last run did not stop cleanly,
        including a start that never got past waiting for Telegram: on 2026-10-06 the bot sat
        5 min without network after a reboot, systemd killed it, and nobody was told."""
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self._unclean = self.marker.exists()
        self.marker.write_text(dt.datetime.now(dt.UTC).isoformat())

    async def post_init(self, app: Application) -> None:
        self._started = True
        if getattr(self, "_unclean", False):
            self.alert("me he reiniciado tras una parada inesperada.", key="restart")
        self._tasks.append(asyncio.create_task(self._housekeeping(app.bot)))
        interval = watchdog_interval_s()
        if interval:
            self._tasks.append(asyncio.create_task(self._watchdog(interval)))
        notify("READY=1")
        log.info("bot ready: instance=%s @%s", self.settings.instance, app.bot.username)

    async def post_shutdown(self, app: Application) -> None:
        for t in self._tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await self.analyzer.router.client.aclose()
        # Only a bot that really started may clear the crash marker: if startup failed before
        # post_init (no network, start timeout), the marker survives to raise the alert.
        if getattr(self, "_started", False):
            with contextlib.suppress(FileNotFoundError):
                self.marker.unlink()
        log.info("bot stopped cleanly")

    async def _watchdog(self, interval: float) -> None:
        while True:
            notify("WATCHDOG=1")
            await asyncio.sleep(interval)

    async def ask_followups(self, bot, now: dt.datetime | None = None) -> int:
        """At most one «¿qué pasó al final?» per user per run, for their latest report aged
        followup_min_days..followup_max_days. The other due cases of that user are not asked.
        Outside the daytime hours nothing is asked nor settled: the next run does it."""
        hour = (now or dt.datetime.now()).astimezone().hour
        if not self.settings.followup_from_hour <= hour < self.settings.followup_until_hour:
            return 0
        due = self.store.due_followups(
            min_age=dt.timedelta(days=self.settings.followup_min_days),
            max_age=dt.timedelta(days=self.settings.followup_max_days),
        )
        latest: dict[int, dict] = {}
        for row in due:  # newest first
            latest.setdefault(row["tg_user_id"], row)
        sent, asked = 0, []
        for uid, row in latest.items():
            if not self.limiter.is_allowed_user(uid):
                continue
            day = row["created_at"].astimezone().strftime("%d-%m")
            # Asked in the report's language.
            t = texts_en if row.get("lang") == "en" else texts
            level = t.FOLLOWUP_LEVEL.get(row["level"], row["level"])
            cid = row["case_id"]
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(t.BUTTON_OUT_SCAM, callback_data=f"out:{cid}:scam")],
                    [InlineKeyboardButton(t.BUTTON_OUT_LEGIT, callback_data=f"out:{cid}:legit")],
                    [
                        InlineKeyboardButton(
                            t.BUTTON_OUT_UNKNOWN, callback_data=f"out:{cid}:unknown"
                        )
                    ],
                ]
            )
            try:
                await bot.send_message(
                    uid, t.FOLLOWUP.format(level=level, day=day), reply_markup=kb
                )
                sent += 1
                asked.append(cid)
            except TelegramError as e:  # blocked the bot, deleted the chat...
                log.info("follow-up to user %s not delivered: %s", uid, type(e).__name__)
        # Every due case is settled: asked once, or closed without asking; never again.
        self.store.mark_followups_asked(asked)
        self.store.mark_followups_asked(
            (r["case_id"] for r in due if r["case_id"] not in asked), skipped=True
        )
        if sent:
            log.info("follow-up: asked %d user(s)", sent)
        return sent

    async def _housekeeping(self, bot) -> None:
        while True:
            try:
                await self.ask_followups(bot)
            except Exception:
                log.exception("follow-ups failed")
            try:
                n = self.store.purge_expired()
                if n:
                    log.info("retention: purged personal data of %d cases", n)
                now = dt.datetime.now(dt.UTC)
                for uid in [u for u, d in self.drafts.items() if now - d.created_at > DRAFT_TTL]:
                    if uid not in self.busy:
                        self.reset(uid, forget=False)
                for uid in [u for u, (_, t) in self.previous.items() if now - t > FOLLOW_UP_TTL]:
                    self.previous.pop(uid, None)
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(3600)

    def build(self) -> Application:
        app = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token.get_secret_value())
            .concurrent_updates(True)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )
        # Spanish commands and their English twins (14-10-2026).
        app.add_handler(CommandHandler(["start", "ayuda", "help"], self.on_start))
        app.add_handler(CommandHandler(["privacidad", "privacy"], self.on_privacy))
        app.add_handler(CommandHandler(["invitar", "invite"], self.on_invite))
        app.add_handler(CommandHandler(["nuevo", "new"], self.on_new))
        app.add_handler(CommandHandler(["borrar", "delete"], self.on_delete))
        app.add_handler(CommandHandler(["analizar", "analyse", "analyze"], self.on_analyze_cmd))
        app.add_handler(CommandHandler(["ejemplo", "example"], self.on_example))
        app.add_handler(CommandHandler(["english", "espanol", "es", "en"], self.on_lang))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.on_image))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(MessageHandler(~filters.COMMAND, self.on_other))
        app.add_error_handler(self.on_error)
        return app


def run(settings: Settings) -> None:
    store = Store(settings.database_url)
    client = LLMClient(timeout_s=settings.llm_timeout_s, env=settings.provider_env())
    router = LLMRouter(settings, store, client)
    market = MarketStore(settings.market_database_url)
    streets = (
        StreetChecker(
            store, settings.hmac_key.get_secret_value().encode(), base_url=settings.nominatim_url
        )
        if settings.address_check
        else None
    )
    bot = MikiwameBot(settings, store, Analyzer(settings, store, router, market, streets))
    app = bot.build()
    bot.claim_marker()
    # bootstrap_retries=-1: a network outage at startup waits instead of crashing the unit into
    # systemd's start limit (review 2026-10-01; the house link is a phone that can drop).
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
        bootstrap_retries=-1,
    )
