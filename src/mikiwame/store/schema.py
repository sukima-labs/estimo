"""Portable schema (SQLAlchemy Core). SQLite today; Postgres is a DATABASE_URL change.

Personal data lives only in the columns marked PII below, which `purge_expired` empties after
the retention period. Everything else (levels, signal codes, token counts, HMACs) is kept.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns aware UTC, on every backend."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime")
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=dt.UTC)


metadata = MetaData()

cases = Table(
    "cases",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("tg_user_id", BigInteger, nullable=False, index=True),
    Column("created_at", UTCDateTime, nullable=False, index=True),
    Column("status", String(16), nullable=False),  # analyzed | failed
    Column("level", String(8)),  # green | amber | red
    Column("confidence", Float),
    Column("score", Float),
    Column("signal_codes", Text),  # comma-separated, no PII
    Column("hard_rules", Text),  # comma-separated, no PII
    Column("image_count", Integer, nullable=False, default=0),
    Column("error", Text),
    Column("expires_at", UTCDateTime, nullable=False, index=True),
    Column("purged_at", UTCDateTime),
    # PII: emptied by purge_expired.
    Column("case_json", Text),
    Column("verdict_json", Text),
    Column("report_text", Text),
)

votes = Table(
    "votes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String(32), ForeignKey("cases.id", ondelete="CASCADE"), index=True),
    Column("voter", String(32), nullable=False),
    Column("score", Float, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("hard_flag", Boolean, nullable=False),
    Column("signal_codes", Text),
)

# No foreign key on purpose: cost history survives a user deleting their cases.
llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", UTCDateTime, nullable=False, index=True),
    Column("case_id", String(32), index=True),
    Column("role", String(16), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model", String(64), nullable=False),
    Column("protocol", String(16), nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cached_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("error", Text),
)

# pHash of property photos per case (not personal data; deleted with the case on /borrar).
photos = Table(
    "photos",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String(32), ForeignKey("cases.id", ondelete="CASCADE"), index=True),
    Column("phash", String(16), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)

# Modus operandi, one anonymous row per analysed case (added 2026-10-01). No user id, no
# advertiser identifier, no free text and no foreign key: it survives retention and /borrar
# as statistics. Date at day granularity.
case_features = Table(
    "case_features",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("day", String(10), nullable=False, index=True),
    Column("level", String(8), nullable=False),
    Column("signal_codes", Text),
    Column("hard_rules", Text),
    Column("channel", String(16)),
    Column("property_type", String(16)),
    Column("iban_countries", Text),
    Column("phone_countries", Text),
    Column("amounts_eur", Text),  # JSON list of amounts the advertiser mentioned
    Column("first_money_msg", Integer),  # 1-based advertiser message asking for money, or null
    Column("advertiser_msgs", Integer),
    Column("listing_known", Boolean),
    Column("listing_removed", Boolean),
)

# Nominatim answers (geo.py): does this street exist in this municipality? Keyed by an HMAC of
# the street key, no case link; re-checked after 30 days.
street_checks = Table(
    "street_checks",
    metadata,
    Column("key_hmac", String(64), primary_key=True),
    Column("found", Boolean, nullable=False),
    Column("checked_at", UTCDateTime, nullable=False),
)

# Follow-up question a few days after the report («¿qué pasó al final?», Patricio 01-10-2026).
# Linked to the case (deleted with it on /borrar) and to its anonymous features row.
followups = Table(
    "followups",
    metadata,
    Column("case_id", String(32), ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True),
    Column("feature_id", Integer),
    Column("created_at", UTCDateTime, nullable=False),
    Column("asked_at", UTCDateTime),
    Column("answered_at", UTCDateTime),
    # Language of the report, so the question is asked in it ("es" when missing).
    Column("lang", String(2)),
)

# A language the user chose by hand (/english, /espanol), over their Telegram app's. Deleted by
# /borrar with their cases.
user_prefs = Table(
    "user_prefs",
    metadata,
    Column("tg_user_id", BigInteger, primary_key=True),
    Column("lang", String(2), nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

# The answer, attached to the anonymous features row: no user, no case id, survives retention and
# /borrar like the features themselves. The real-world ground truth for precision.
feature_outcomes = Table(
    "feature_outcomes",
    metadata,
    Column("feature_id", Integer, primary_key=True),
    Column("outcome", String(8), nullable=False),  # scam | legit | unknown
    Column("answered_day", String(10), nullable=False),
)

# Redacted cases the user explicitly agreed to keep for research. No user id; no expiry.
research_cases = Table(
    "research_cases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kept_at", UTCDateTime, nullable=False),
    Column("level", String(8)),
    Column("signal_codes", Text),
    Column("case_json", Text),
)

# One row per analysis attempt, for rate limits. Survives /borrar (or deleting your cases would
# reset your limit, review 2026-10-01) and is purged after 7 days.
usage = Table(
    "usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tg_user_id", BigInteger, nullable=False, index=True),
    Column("created_at", UTCDateTime, nullable=False, index=True),
)

# Invite-only beta (Patricio 01-10-2026: «lo que sea mejor para mí y para el proyecto»). Members
# get in with a code from someone already inside; the invite tree is the trust model of the
# registry: reports from one branch count once (voters/recidivism.py). Roots are the env
# allowlist (MIKIWAME_ALLOWED_USERS) and are not rows here.
members = Table(
    "members",
    metadata,
    Column("tg_user_id", BigInteger, primary_key=True),
    Column("invited_by", BigInteger, index=True),
    Column("joined_at", UTCDateTime, nullable=False),
    Column("invites_left", Integer, nullable=False, default=0),
    Column("revoked_at", UTCDateTime),
    # Beta budget: analyses run so far, personal quota (None = the default), last analysis.
    Column("analyses_used", Integer, nullable=False, default=0, server_default="0"),
    Column("quota", Integer),
    Column("last_active_at", UTCDateTime),
    # Code of the group invite (campaigns) they joined through; None for personal invites.
    Column("campaign", String(16)),
)

invites = Table(
    "invites",
    metadata,
    Column("code", String(16), primary_key=True),
    Column("created_by", BigInteger, nullable=False, index=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
    Column("used_by", BigInteger),
    Column("used_at", UTCDateTime),
)

# Group invites (Patricio 01-10-2026: invitations posted in Telegram and WhatsApp groups). One
# link per group with a few uses; everyone who joins through it, and whoever they invite, is a
# single branch of the trust tree (strangers from one group must not add up to two reports).
# Unused uses of an open campaign hold beta seats, like pending invites.
campaigns = Table(
    "campaigns",
    metadata,
    Column("code", String(16), primary_key=True),
    Column("name", String(64), nullable=False, unique=True),
    Column("created_by", BigInteger, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
    Column("max_uses", Integer, nullable=False),
    Column("uses", Integer, nullable=False, default=0, server_default="0"),
    Column("closed_at", UTCDateTime),
)

# Registry entries a landlord disputed (info@patriciollorens.com): never counted again in
# recidivism.
disputes = Table(
    "disputes",
    metadata,
    Column("kind", String(16), primary_key=True),
    Column("hmac", String(64), primary_key=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("note", Text),
)

indicators = Table(
    "indicators",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(16), nullable=False),  # phone | iban | name | business | email | listing
    Column("hmac", String(64), nullable=False),
    Column("first_seen", UTCDateTime, nullable=False),
    Column("last_seen", UTCDateTime, nullable=False),
    UniqueConstraint("kind", "hmac", name="uq_indicator"),
)

case_indicators = Table(
    "case_indicators",
    metadata,
    Column("case_id", String(32), ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "indicator_id",
        Integer,
        ForeignKey("indicators.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)
