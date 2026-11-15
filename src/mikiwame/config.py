"""Runtime settings, read from the environment and /opt/mikiwame/.env.

Every model role is "provider:model" and can be switched by environment variable without
touching code. Startup fails if any configured model is refused by the privacy policy.
"""

from __future__ import annotations

import os
import re
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from mikiwame import PROJECT_DIR
from mikiwame.llm.registry import ModelRef, resolve

Role = Literal["vision", "text", "adversarial"]
ROLES: tuple[Role, ...] = ("vision", "text", "adversarial")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_prefix="MIKIWAME_",
        extra="ignore",
        populate_by_name=True,
        # A validation error must never print a secret (e.g. a too-short HMAC key) to the journal.
        hide_input_in_errors=True,
    )

    # Credentials (no prefix).
    telegram_bot_token: SecretStr = Field(validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN"))
    opencode_api_key: SecretStr = Field(validation_alias=AliasChoices("OPENCODE_API_KEY"))
    opencode_base_url: str = Field(
        "https://opencode.ai/zen/go/v1", validation_alias=AliasChoices("OPENCODE_BASE_URL")
    )

    # HMAC key for the indicator base. Losing or rotating it breaks recidivism matching.
    hmac_key: SecretStr

    # Which bot this process is (systemd instance: mikiwame@<instance>). Names its crash marker
    # and prefixes its admin alerts.
    instance: str = "default"
    admin_chat_id: int | None = None
    allowed_users: Annotated[frozenset[int], NoDecode] = frozenset()

    database_url: str = f"sqlite:///{PROJECT_DIR / 'var' / 'mikiwame.db'}"
    # Optional mirror of rental listings (a local SQLite file; no sync client ships here).
    market_database_url: str = f"sqlite:///{PROJECT_DIR / 'var' / 'market.db'}"

    # Roles that must not reason get models whose no-reasoning knob works (registry.NO_THINK).
    # No Chinese-origin model (Patricio, 2026-10-06): GPT Luna for every role, the older Luna as
    # fallback (its own monthly cap on the plan). Both read screenshots (measured 2026-10-06).
    model_vision: str = "opencode-go:gpt-6-luna"
    model_vision_fallback: str | None = "opencode-go:gpt-5.6-luna"
    model_text: str = "opencode-go:gpt-6-luna"
    model_text_fallback: str | None = "opencode-go:gpt-5.6-luna"
    model_adversarial: str = "opencode-go:gpt-6-luna"
    model_adversarial_fallback: str | None = "opencode-go:gpt-5.6-luna"

    # Daily spend on a role's primary model; past it the role switches to its fallback.
    budget_daily_usd_vision: float = 0.20
    budget_daily_usd_text: float = 0.20
    budget_daily_usd_adversarial: float = 0.20
    # Hard stop for all LLM spend in a rolling 24 h: new analyses are refused past it.
    budget_daily_usd_total: float = 0.50
    # Rolling 30-day spend per model (all roles); past it the model is skipped. Go caps each GPT
    # Luna at $15 a month per account, shared with other services: this keeps us to a fifth
    # of it. Counted per instance (each has its own database).
    budget_monthly_usd_per_model: float = 3.00

    # Invite-only beta: invites each new member gets, and how long a code is valid.
    invites_per_member: int = 2
    invite_ttl_days: int = 7
    # Beta size (Patricio 01-10: «que no se escale demasiado»). 0 reds in n real legit cases
    # bound the false-red rate under 3/n (95 %): 100 confirmed legit for < 3 %. With ~40-50 % of
    # follow-ups answered for sure and some scams among them, that is ~220-280 analyses: ~20
    # active people at ~7 a month for ~2 months. Assumptions, not data: re-measure after two
    # weeks. Seats = active members (used it or joined in the last member_idle_days) + pending
    # invites; idle members keep access but free their seat.
    max_members: int = 40
    member_idle_days: int = 30
    # Analyses each member may run during the whole beta (roots: no limit). Average need ~7-15;
    # Patricio raises it per person with `mikiwame member quota <id> <n>`.
    member_quota: int = 30

    # Open access (decided 01-10-2026, Patricio delegated it: invites were too much friction, and
    # a link in a group post is muted as spam). Anyone can start the bot as a public member:
    # no seat, and all of them are one branch of the trust tree. An invite upgrades them to a
    # member. Off: invite-only.
    open_access: bool = False
    # Analyses each public member may run in total; None: no total, only the daily brake
    # (Patricio, 21-10-2026: free for now, quotas again if use and costs grow).
    public_quota: int | None = None

    # Anti-burst brake (a runaway loop or one very busy day), not the beta budget. With no
    # public quota it is also the security limit: a scammer cannot retry a script without end.
    limit_user_daily: int = 5
    # Follow-up question («¿qué pasó al final?»): asked between these ages of the report.
    followup_min_days: int = 5
    followup_max_days: int = 7
    # ...and only in daytime, local hours [from, until): it buzzes the phone. The 5-7 day window
    # is 48 h wide, so no case misses its daytime slot (one went out at 01:36 on 10-10).
    followup_from_hour: int = 10
    followup_until_hour: int = 21
    # Open access: a global brake on analyses per day (the money brakes are the budgets above).
    limit_global_daily: int = 100
    # Follow-ups add to the previous case, so leave room for the answer screenshots.
    max_images: int = 8
    max_text_chars: int = 8000
    retention_days: int = 30

    # Website (`mikiwame web`, decided 14-10-2026): the same analysis without Telegram, behind
    # a reverse proxy that terminates TLS. Plain HTTP here.
    web_host: str = "0.0.0.0"
    web_port: int = 8770
    # Peers whose X-Forwarded-For is believed (the reverse proxy's address). Anyone else is
    # the client.
    web_trusted_proxies: Annotated[frozenset[str], NoDecode] = frozenset({"127.0.0.1"})
    # Visitors are counted per IP (HMAC). Mobile carriers put many people behind one address,
    # so this is a brake per day, never a quota for life.
    web_limit_visitor_daily: int = 5
    # All website analyses in 24 h (they also count towards limit_global_daily).
    web_limit_daily: int = 50
    web_max_image_mb: int = 10
    web_concurrency: int = 3

    # Only used to show costs in euros; update when it drifts.
    usd_eur: float = 0.86

    # Street check against OpenStreetMap's Nominatim (geo.py): 1 request/s, our User-Agent.
    address_check: bool = True
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    # The house SearXNG (ct/101): text search for businesses and domains only, never for
    # particulars' data. None = off.
    searxng_url: str | None = None

    llm_timeout_s: float = 90.0
    # Whether each role lets the model reason (more tokens). Measured in evaluation/ with GPT-6
    # Luna, 2026-10-06. Adversary: without reasoning it softened 10/12 wrong reds, with it 12/12
    # (kept 8/8 right ones either way). Text and adversary both reasoning, oracle x2: 0 legit in
    # red, ideal 0.94 (0.89 with the adversary alone), ~0.0005 $ and ~8 s per report.
    think_vision: bool = False
    think_text: bool = True
    think_adversarial: bool = True
    # GPT-6 Luna's default effort slowed down (06-10: 3.6 s per review; 17/19-10: 16 s on
    # average, some 90 s timeouts, a lone web case 88 s). Adversary benchmark x2 (19-10): "low"
    # softened 12/12 wrong reds and kept 10/10 right ones in 3.9 s (max 5.8), the same as
    # "medium" (13.9 s, max 54). Empty = the model's default.
    effort_adversarial: str | None = "low"

    @field_validator("allowed_users", mode="before")
    @classmethod
    def _parse_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return frozenset(int(x) for x in v.replace(";", ",").split(",") if x.strip())
        return v

    @field_validator("web_trusted_proxies", mode="before")
    @classmethod
    def _parse_hosts(cls, v: object) -> object:
        if isinstance(v, str):
            return frozenset(x.strip() for x in v.replace(";", ",").split(",") if x.strip())
        return v

    @field_validator("hmac_key")
    @classmethod
    def _check_hmac_key(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("MIKIWAME_HMAC_KEY must be at least 32 characters")
        return v

    def model_spec(self, role: Role, *, fallback: bool = False) -> str | None:
        return getattr(self, f"model_{role}_fallback" if fallback else f"model_{role}")

    def role_budget_usd(self, role: Role) -> float:
        return getattr(self, f"budget_daily_usd_{role}")

    @cached_property
    def models(self) -> dict[tuple[Role, bool], ModelRef]:
        """Resolved models per (role, is_fallback). Raises if any is refused."""
        out: dict[tuple[Role, bool], ModelRef] = {}
        for role in ROLES:
            if not (self.model_spec(role) or "").strip():
                raise ValueError(f"MIKIWAME_MODEL_{role.upper()} must not be empty")
            for fb in (False, True):
                spec = self.model_spec(role, fallback=fb)
                if spec and spec.strip():
                    out[(role, fb)] = resolve(spec, need_vision=(role == "vision"))
        return out

    def validate_models(self) -> None:
        _ = self.models

    def provider_env(self) -> dict[str, str]:
        """Credentials keyed by the env names the provider registry uses."""
        return {
            "OPENCODE_BASE_URL": self.opencode_base_url,
            "OPENCODE_API_KEY": self.opencode_api_key.get_secret_value(),
        }


_INSTANCE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def env_files(instance: str | None = None) -> tuple[Path, ...]:
    """The shared .env, then .env.<instance> on top (later files win; real env vars win over
    both). Instances share model keys and the HMAC key; each has its own bot token and DB."""
    instance = instance if instance is not None else os.environ.get("MIKIWAME_INSTANCE", "")
    files = [PROJECT_DIR / ".env"]
    if instance:
        if not _INSTANCE_RE.match(instance):
            raise ValueError(f"bad instance name {instance!r}")
        path = PROJECT_DIR / f".env.{instance}"
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist")
        files.append(path)
    return tuple(files)


def load_settings(**overrides: object) -> Settings:
    overrides.setdefault("_env_file", env_files())
    settings = Settings(**overrides)
    settings.validate_models()
    return settings
