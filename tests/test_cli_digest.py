import argparse
import logging
from types import SimpleNamespace

from mikiwame import cli


def test_digest_send_logs_the_delivery_channel(monkeypatch, capsys):
    import mikiwame.bot.alert as alert
    import mikiwame.config as config
    import mikiwame.digest as digest

    settings = SimpleNamespace(
        telegram_bot_token=SimpleNamespace(get_secret_value=lambda: "t"), admin_chat_id=1
    )
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(digest, "build", lambda s, days: "body")

    def fake_send(token, chat_id, text, **kw):
        logging.getLogger("mikiwame.bot.alert").info("admin alert delivered via WhatsApp")
        return True

    monkeypatch.setattr(alert, "send_admin", fake_send)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.WARNING)
    assert cli._digest(argparse.Namespace(days=7, send=True)) == 0
    out = capsys.readouterr().out
    assert "delivered via WhatsApp" in out
    assert "digest: 4 characters, delivered" in out
