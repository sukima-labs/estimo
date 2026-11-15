from __future__ import annotations

import datetime as dt

import pytest

from mikiwame.llm.registry import OPENCODE_GO, ModelInfo, RefusedModel, UnknownModel, resolve
from tests.conftest import make_settings


def test_defaults_resolve(settings):
    settings.validate_models()
    assert settings.models[("vision", False)].model.vision
    assert settings.allowed_users == frozenset({1, 2})
    # Patricio, 2026-10-06: no Chinese-origin model anywhere; GPT Luna for every role.
    assert {ref.model.id for ref in settings.models.values()} == {"gpt-6-luna", "gpt-5.6-luna"}


CHINESE = (
    "glm-",
    "kimi-",
    "longcat-",
    "mimo-",
    "hy",
    "deepseek-",
    "minimax-",
    "qwen",
    "space-",
    "step-",
)


def test_every_chinese_origin_model_is_refused():
    ids = [i for i in OPENCODE_GO.models if i.startswith(CHINESE)]
    assert len(ids) == 25
    for model_id in ids:
        with pytest.raises(RefusedModel, match="Chinese-origin"):
            resolve(f"opencode-go:{model_id}", today=dt.date(2026, 10, 6))
    # Including the defaults this project used until 2026-10-06, when set by environment.
    for spec in ("qwen3.8-flash", "minimax-m3", "qwen3.7-plus", "qwen3.8-max"):
        with pytest.raises(RefusedModel):
            make_settings(model_text=f"opencode-go:{spec}").validate_models()


def test_only_listed_models_are_usable():
    usable = {i for i, m in OPENCODE_GO.models.items() if m.refusal(dt.date(2026, 10, 6)) is None}
    assert usable == {"gpt-6-luna", "gpt-5.6-luna"}
    # Claude Haiku 5.5 keeps 30 days like GPT Luna: refused until Patricio accepts it too.
    with pytest.raises(RefusedModel, match="30 days"):
        resolve("opencode-go:claude-haiku-5-5", today=dt.date(2026, 10, 10))


@pytest.mark.parametrize(
    "spec",
    [
        "opencode-go:muse-spark-1.3-contributor",  # trains on requests
        "opencode-go:grok-4.7",  # 30-day retention, accepted only for GPT Luna
        "opencode-go:grok-4.6",
    ],
)
def test_privacy_refusals(spec):
    with pytest.raises(RefusedModel):
        resolve(spec)


def test_undocumented_model_is_unknown():
    # Listed by /models on 2026-09-30 but absent from the docs and their privacy table.
    with pytest.raises(UnknownModel):
        resolve("opencode-go:deepseek-flash")


def test_zdr_expiry():
    m = ModelInfo("x", "chat", False, 1, 1, 1, zdr_until=dt.date(2026, 10, 31))
    assert m.refusal(dt.date(2026, 10, 31)) is None
    assert "ZDR" in m.refusal(dt.date(2026, 11, 1))


def test_vision_role_requires_vision(monkeypatch):
    blind = ModelInfo("blind", "responses", False, 1, 1, 1)
    monkeypatch.setitem(OPENCODE_GO.models, "blind", blind)
    assert resolve("opencode-go:blind")
    with pytest.raises(RefusedModel, match="vision"):
        resolve("opencode-go:blind", need_vision=True)


def test_config_refuses_bad_model():
    s = make_settings(model_adversarial="opencode-go:grok-4.7")
    with pytest.raises(RefusedModel):
        s.validate_models()


def test_short_hmac_key_rejected():
    with pytest.raises(ValueError):
        make_settings(hmac_key="short")


def test_cost():
    m = resolve("opencode-go:gpt-6-luna").model
    assert m.cost_usd(1_000_000, 0) == pytest.approx(0.10)
    assert m.cost_usd(1000, 1000, cached_tokens=1000) == pytest.approx((0.01 + 0.50) / 1000)


def test_instance_env_file_overrides_shared(tmp_path, monkeypatch):
    from mikiwame import config

    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=shared\nOPENCODE_API_KEY=k\nMIKIWAME_HMAC_KEY=" + "h" * 64 + "\n"
    )
    (tmp_path / ".env.estimo").write_text(
        "TELEGRAM_BOT_TOKEN=prod\nMIKIWAME_INSTANCE=estimo\nMIKIWAME_DATABASE_URL=sqlite:///x\n"
    )
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    for var in ("TELEGRAM_BOT_TOKEN", "MIKIWAME_INSTANCE", "MIKIWAME_DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    shared = config.Settings(_env_file=config.env_files(""))
    prod = config.Settings(_env_file=config.env_files("estimo"))
    assert shared.telegram_bot_token.get_secret_value() == "shared"
    assert prod.telegram_bot_token.get_secret_value() == "prod"
    assert prod.instance == "estimo" and prod.database_url == "sqlite:///x"
    assert prod.opencode_api_key.get_secret_value() == "k"  # inherited from the shared file
    with pytest.raises(FileNotFoundError):
        config.env_files("nope")
    with pytest.raises(ValueError):
        config.env_files("../etc")
