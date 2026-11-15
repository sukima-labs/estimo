"""Archive mode (project closed on 2026-11-15): the bot answers every message with one fixed
text that points to the guides, and does nothing else.

No database, no model, no HMAC key, no admin alerts and nothing is stored: it needs only the
bot token. It runs for a few weeks so that people who still have the bot open learn where to go.
"""

from __future__ import annotations

import logging
import time

from dotenv import dotenv_values
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from mikiwame.bot.app import app_lang
from mikiwame.config import env_files

log = logging.getLogger(__name__)

SITE = "https://sukimalabs.com/estimo/"
TEXTS = {
    "es": (
        "¿Es timo? cerró el 15-11-2026: casi nadie llegó a usarlo y ya no analizo anuncios "
        "ni guardo nada de lo que me mandes.\n\n"
        f"Las guías sobre estafas de alquiler siguen aquí: {SITE}\n"
        f"Si ya has pagado y crees que es una estafa: {SITE}guias/ya-he-pagado/\n"
        "Ayuda gratuita en ciberseguridad (INCIBE): 017."
    ),
    "en": (
        "¿Es timo? closed on 15-11-2026: almost nobody used it, so I no longer analyse "
        "listings or keep anything you send me.\n\n"
        f"The guides on rental scams stay online: {SITE}en/\n"
        f"If you have already paid and think it is a scam: {SITE}en/guides/already-paid/\n"
        "Free cybersecurity help in Spain (INCIBE): 017."
    ),
}
# One answer per chat per minute: a flood of messages gets one reply.
THROTTLE_S = 60.0
_last: dict[int, float] = {}


def text_for(code: str | None) -> str:
    return TEXTS[app_lang(code)]


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if chat is None or chat.type != "private":
        return
    now = time.monotonic()
    if now - _last.get(chat.id, -THROTTLE_S) < THROTTLE_S:
        return
    _last[chat.id] = now
    if len(_last) > 5000:  # forget old chats
        for k in [k for k, t in _last.items() if now - t > THROTTLE_S]:
            del _last[k]
    await context.bot.send_message(
        chat.id, text_for(getattr(user, "language_code", None)), disable_web_page_preview=True
    )
    log.info("archived reply sent")  # no user id, no content


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query is not None:
        await update.callback_query.answer()
    await reply(update, context)


def token() -> str:
    values: dict[str, str | None] = {}
    for f in env_files():
        values.update(dotenv_values(f))
    value = values.get("TELEGRAM_BOT_TOKEN")
    if not value:
        raise SystemExit("archived bot: TELEGRAM_BOT_TOKEN is not set")
    return value


def build(bot_token: str) -> Application:
    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.ALL, reply))
    return app


def run() -> None:
    build(token()).run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        bootstrap_retries=-1,
    )
