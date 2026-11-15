import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mikiwame.bot import archived


def _update(chat_id=5, chat_type="private", lang="es"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(language_code=lang),
        callback_query=None,
    )


def _context():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


def test_texts_point_to_the_guides_in_both_languages():
    assert "sukimalabs.com/estimo/guias/ya-he-pagado/" in archived.text_for("es")
    assert "sukimalabs.com/estimo/en/guides/already-paid/" in archived.text_for("en-GB")
    assert archived.text_for(None) == archived.TEXTS["es"]
    assert archived.text_for("fr") == archived.TEXTS["en"]
    for t in archived.TEXTS.values():
        assert "¿Es timo?" in t and "017" in t


def test_one_reply_per_chat_per_minute_and_only_in_private(monkeypatch):
    monkeypatch.setattr(archived, "_last", {})
    ctx = _context()
    asyncio.run(archived.reply(_update(chat_id=1), ctx))
    asyncio.run(archived.reply(_update(chat_id=1), ctx))  # throttled
    asyncio.run(archived.reply(_update(chat_id=2, lang="en"), ctx))
    asyncio.run(archived.reply(_update(chat_id=3, chat_type="group"), ctx))  # ignored
    sent = ctx.bot.send_message.await_args_list
    assert [c.args[0] for c in sent] == [1, 2]
    assert sent[1].args[1] == archived.TEXTS["en"]


def test_it_needs_only_the_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=123:abc\n")
    monkeypatch.setattr(archived, "env_files", lambda: (env,))
    assert archived.token() == "123:abc"
    assert archived.build("123:abc") is not None
