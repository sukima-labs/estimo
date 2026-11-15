"""Known models per provider: protocol, capabilities, prices and privacy status.

This table is data copied from the provider's docs, and it ages. Source for opencode-go:
https://opencode.ai/v2/docs/console/go/ (moved from /docs/go/, which is a 404 since 2026-10-12;
re-read that day: privacy and prices unchanged for every model listed here, step-5-preview-free
is new, kimi-k2.6 is absent on purpose). Re-read the docs before trusting a price, and before
changing anything under `blocked` or `zdr_until`.

Model policy (see README.md): we handle third-party personal data, so a model is only usable
when the provider's privacy table says "Not used" for training, and
- no Chinese-origin model at all (Patricio, 2026-10-06): DeepSeek, Qwen, GLM, Kimi, MiniMax,
  MiMo, LongCat, Hunyuan (hy*), Space Bunny and StepFun (step-*) are blocked whatever their
  retention;
- retention "0 days", except GPT Luna: Patricio accepted on 2026-10-06 that OpenAI keeps its
  abuse-monitoring logs for up to 30 days (said in /privacidad). Grok, also 30 days, is not
  accepted.
Anything else is listed with a `blocked` reason so the config refuses to start with it. Ids
that the `/models` endpoint returns but the docs do not document (e.g. `deepseek-flash`) are
simply absent: unknown means refused.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

Protocol = Literal["chat", "messages", "responses"]


@dataclass(frozen=True)
class ModelInfo:
    id: str
    protocol: Protocol
    # Vision support measured with one synthetic WhatsApp screenshot (on 2026-09-30;
    # GPT Luna by us on 2026-10-06). False means "not verified", not "cannot".
    vision: bool
    # USD per 1M tokens. For models with peak/off-peak pricing we store the peak price.
    price_in: float
    price_out: float
    price_cached: float
    blocked: str | None = None
    # Zero-data-retention agreement that must be renewed; refused after this date.
    zdr_until: dt.date | None = None

    def cost_usd(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        uncached = max(input_tokens - cached_tokens, 0)
        return (
            uncached * self.price_in
            + cached_tokens * self.price_cached
            + output_tokens * self.price_out
        ) / 1_000_000

    def refusal(self, today: dt.date | None = None) -> str | None:
        """Why this model must not be used for third-party data, or None if it may."""
        if self.blocked:
            return self.blocked
        today = today or dt.date.today()
        if self.zdr_until and today > self.zdr_until:
            return f"ZDR agreement expired on {self.zdr_until.isoformat()}: re-check the docs"
        return None


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    base_url_env: str
    api_key_env: str
    models: dict[str, ModelInfo]


_TRAINS = "provider may train on requests"
_RETAINS = "provider retains data for 30 days (accepted only for GPT Luna, Patricio 2026-10-06)"
_CHINESE = "Chinese-origin model: none anywhere in the system (Patricio, 2026-10-06)"
_DEEPSEEK_ZDR = dt.date(2026, 10, 31)


def _m(*args, **kwargs) -> ModelInfo:
    return ModelInfo(*args, **kwargs)


OPENCODE_GO = ProviderInfo(
    name="opencode-go",
    base_url_env="OPENCODE_BASE_URL",
    api_key_env="OPENCODE_API_KEY",
    models={
        m.id: m
        for m in [
            _m("glm-5.3-flash", "chat", True, 0.15, 0.50, 0.03, blocked=_CHINESE),
            _m("glm-5.3", "chat", False, 1.40, 4.40, 0.26, blocked=_CHINESE),
            _m("glm-5.2", "chat", False, 1.40, 4.40, 0.26, blocked=_CHINESE),
            _m("kimi-k3", "chat", True, 3.00, 15.00, 0.30, blocked=_CHINESE),
            _m("kimi-k2.7-code", "chat", True, 0.95, 4.00, 0.19, blocked=_CHINESE),
            _m("longcat-2.0", "chat", False, 0.30, 1.20, 0.006, blocked=_CHINESE),
            _m("mimo-v2.6-flash", "chat", False, 0.14, 0.28, 0.0028, blocked=_CHINESE),
            _m("mimo-v2.6-pro", "chat", False, 0.435, 0.87, 0.003625, blocked=_CHINESE),
            _m("mimo-v2.5", "chat", True, 0.14, 0.28, 0.0028, blocked=_CHINESE),
            _m("mimo-v2.5-pro", "chat", False, 0.435, 0.87, 0.003625, blocked=_CHINESE),
            _m("hy3", "chat", False, 0.14, 0.58, 0.035, blocked=_CHINESE),
            _m(
                "deepseek-v4.1-flash",
                "chat",
                False,
                0.30,
                1.20,
                0.006,
                zdr_until=_DEEPSEEK_ZDR,
                blocked=_CHINESE,
            ),
            _m(
                "deepseek-v4-flash",
                "chat",
                False,
                0.30,
                1.20,
                0.006,
                zdr_until=_DEEPSEEK_ZDR,
                blocked=_CHINESE,
            ),
            _m(
                "deepseek-v4-pro",
                "chat",
                False,
                1.32,
                3.96,
                0.044,
                zdr_until=_DEEPSEEK_ZDR,
                blocked=_CHINESE,
            ),
            _m(
                "deepseek-v4-flash-vision-exp",
                "chat",
                True,
                0.30,
                1.20,
                0.006,
                zdr_until=_DEEPSEEK_ZDR,
                blocked=_CHINESE,
            ),
            _m("minimax-m3", "messages", True, 0.30, 1.20, 0.06, blocked=_CHINESE),
            _m("minimax-m2.7", "messages", False, 0.30, 1.20, 0.06, blocked=_CHINESE),
            _m("qwen3.8-max", "messages", True, 2.00, 6.00, 0.25, blocked=_CHINESE),
            _m("qwen3.8-flash", "messages", True, 0.15, 0.47, 0.016, blocked=_CHINESE),
            _m("qwen3.7-plus", "messages", True, 0.40, 1.60, 0.04, blocked=_CHINESE),
            _m("hy4-preview", "chat", False, 0.834, 2.501, 0.042, blocked=_CHINESE),
            _m("space-bunny-free", "chat", False, 0, 0, 0, blocked=_CHINESE),
            _m("space-bunny", "chat", False, 0.15, 0.60, 0, blocked=_CHINESE),
            _m("longcat-2.5-preview-free", "chat", False, 0, 0, 0, blocked=_CHINESE),
            # StepFun (Shanghai); listed 2026-10-12 as "Not used", 0 days.
            _m("step-5-preview-free", "chat", False, 0, 0, 0, blocked=_CHINESE),
            _m(
                "muse-spark-1.3-contributor", "responses", False, 0.10, 0.20, 0.002, blocked=_TRAINS
            ),
            _m(
                "muse-spark-1.2-contributor", "responses", False, 0.10, 0.20, 0.002, blocked=_TRAINS
            ),
            # Doc of 2026-10-08: not used for training, 30 days retention (like GPT Luna); cached
            # read documented on 2026-10-12. Needs Patricio's OK on the retention first.
            _m("claude-haiku-5-5", "messages", False, 0.10, 0.50, 0.01, blocked=_RETAINS),
            _m("grok-4.7", "responses", False, 2.00, 6.00, 0.50, blocked=_RETAINS),
            _m("grok-4.6", "responses", False, 2.00, 6.00, 0.50, blocked=_RETAINS),
            # Only on /responses: chat/completions gives 400 (2026-10-06).
            _m("gpt-6-luna", "responses", True, 0.10, 0.50, 0.01),
            _m("gpt-5.6-luna", "responses", True, 0.20, 1.20, 0.02),
        ]
    },
)

PROVIDERS: dict[str, ProviderInfo] = {OPENCODE_GO.name: OPENCODE_GO}

# Extra body fields that turn reasoning off, per model. Each family uses a different knob, and
# the provider can stop accepting one without notice; the client drops a knob that gets a 400.
# Listed = measured working. GPT Luna (2026-10-06): effort "none" answers with 0 reasoning
# tokens; "minimal" is a 400. Without it, reasoning ate all 600 tokens of a screenshot reading.
NO_THINK: dict[str, dict[str, object]] = {
    "gpt-6-luna": {"reasoning": {"effort": "none"}},
    "gpt-5.6-luna": {"reasoning": {"effort": "none"}},
}


@dataclass(frozen=True)
class ModelRef:
    provider: ProviderInfo
    model: ModelInfo

    @property
    def label(self) -> str:
        return f"{self.provider.name}:{self.model.id}"


class UnknownModel(ValueError):
    pass


class RefusedModel(ValueError):
    pass


def resolve(spec: str, *, need_vision: bool = False, today: dt.date | None = None) -> ModelRef:
    """Turn "provider:model" into a ModelRef, refusing anything the privacy policy forbids."""
    provider_name, sep, model_id = spec.strip().partition(":")
    if not sep or not model_id:
        raise UnknownModel(f"model spec must be 'provider:model', got {spec!r}")
    provider = PROVIDERS.get(provider_name)
    if provider is None:
        raise UnknownModel(f"unknown provider {provider_name!r} (known: {sorted(PROVIDERS)})")
    model = provider.models.get(model_id)
    if model is None:
        raise UnknownModel(f"model {model_id!r} is not documented for {provider_name}")
    reason = model.refusal(today)
    if reason:
        raise RefusedModel(f"{spec} refused: {reason}")
    if need_vision and not model.vision:
        raise RefusedModel(f"{spec} refused: vision not verified for this model")
    return ModelRef(provider, model)
