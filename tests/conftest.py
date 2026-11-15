from __future__ import annotations

import pytest

from mikiwame.config import Settings
from mikiwame.llm.registry import OPENCODE_GO, ModelInfo, ModelRef


@pytest.fixture(autouse=True)
def _no_real_kchat_webhook(tmp_path, monkeypatch):
    """Admin alerts in tests never ssh to the host gate, never read the real webhook in
    ~/.config, nor log under var/."""
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", "off")
    monkeypatch.setenv("MIKIWAME_KCHAT_WEBHOOK_FILE", str(tmp_path / "kchat-webhook"))
    monkeypatch.setattr("mikiwame.bot.alert.UNDELIVERED_LOG", tmp_path / "undelivered.log")


def make_settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        telegram_bot_token="123:test",
        opencode_api_key="test-key",
        opencode_base_url="https://llm.test/v1",
        hmac_key="k" * 64,
        admin_chat_id=1,
        allowed_users="1,2",
        database_url="sqlite://",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def stand_in(protocol: str) -> ModelRef:
    """A model on a protocol no allowed model speaks today (chat, messages): the client keeps
    them for other providers, so they stay tested."""
    return ModelRef(OPENCODE_GO, ModelInfo(f"stand-in-{protocol}", protocol, True, 1.0, 2.0, 0.1))


def responses_ok(text: str = "{}", *, input_tokens: int = 10, output_tokens: int = 5) -> dict:
    """A /responses answer as GPT Luna sends it: a reasoning item, then the message."""
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tokens_details": {"cached_tokens": 0},
        },
    }
