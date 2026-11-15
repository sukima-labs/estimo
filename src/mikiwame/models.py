"""Domain objects: the normalised case, votes, signals and the final verdict."""

from __future__ import annotations

import enum
import uuid
from typing import Literal

from pydantic import BaseModel, Field


def new_case_id() -> str:
    return uuid.uuid4().hex[:16]


class Level(enum.IntEnum):
    """Traffic light. Ordered so that max() gives the most severe."""

    GREEN = 0
    AMBER = 1
    RED = 2

    @property
    def key(self) -> str:
        return {Level.GREEN: "green", Level.AMBER: "amber", Level.RED: "red"}[self]

    @classmethod
    def from_key(cls, key: str) -> Level:
        return {"green": cls.GREEN, "amber": cls.AMBER, "red": cls.RED}[key]


Sender = Literal["advertiser", "user", "unknown"]
PropertyType = Literal["piso", "habitacion", "unknown"]
Channel = Literal[
    "whatsapp",
    "telegram",
    "idealista",
    "fotocasa",
    "milanuncios",
    "habitaclia",
    "pisos.com",
    "wallapop",
    "otro_portal",
    "email",
    "sms",
    "unknown",
]
PORTAL_CHANNELS = {"idealista", "fotocasa", "milanuncios", "habitaclia", "pisos.com", "wallapop",
                   "otro_portal"}  # fmt: skip
CHANNEL_EN = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "idealista": "idealista chat",
    "fotocasa": "Fotocasa chat",
    "milanuncios": "Milanuncios chat",
    "habitaclia": "Habitaclia chat",
    "pisos.com": "pisos.com chat",
    "wallapop": "Wallapop chat",
    "otro_portal": "a portal's chat",
    "email": "email",
    "sms": "SMS",
}
CHANNEL_ES = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "idealista": "chat de idealista",
    "fotocasa": "chat de Fotocasa",
    "milanuncios": "chat de Milanuncios",
    "habitaclia": "chat de Habitaclia",
    "pisos.com": "chat de pisos.com",
    "wallapop": "chat de Wallapop",
    "otro_portal": "chat de un portal",
    "email": "correo electrónico",
    "sms": "SMS",
}


class Message(BaseModel):
    sender: Sender = "unknown"
    text: str


class Case(BaseModel):
    """Everything we know about one listing + conversation, normalised.

    All free text here was written, or may have been written, by the advertiser: it is hostile
    input and must never be treated as instructions.
    """

    case_id: str = Field(default_factory=new_case_id)
    listing_url: str | None = None
    listing_text: str = ""
    pasted_text: str = ""
    chat: list[Message] = Field(default_factory=list)
    advertiser_names: list[str] = Field(default_factory=list)
    # Where the conversation happens, as read from the screenshot (see extract/vision.py).
    channel: Channel = "unknown"
    phones: list[str] = Field(default_factory=list)
    # Phones on the advertiser's side only (chat header, their messages, the ad). The user's own
    # number, if they wrote it, must not count as a signal against the advertiser.
    advertiser_phones: list[str] = Field(default_factory=list)
    advertiser_ibans: list[str] = Field(default_factory=list)
    advertiser_emails: list[str] = Field(default_factory=list)
    ibans: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    price_eur: int | None = None
    property_type: PropertyType = "unknown"
    image_count: int = 0
    # Screenshots that were an identity document (never transcribed): shown inside the
    # advertiser's bubble, and loose photos whose sender cannot be seen. The user's own ID is
    # not counted: it says nothing about the advertiser (review 2026-10-01).
    id_documents: int = 0
    id_documents_unattributed: int = 0
    # Perceptual hashes of the property photos the user sent (see photos.py).
    photo_hashes: list[str] = Field(default_factory=list)
    # What our mirror of the portal says about this listing (see voters/market.py), in Spanish.
    market_facts: str = ""
    market_facts_en: str = ""
    # Price reference for similar listings in the town, in Spanish (see voters/market.py).
    market_reference: str = ""
    market_reference_en: str = ""
    rooms: int | None = None
    municipality: str | None = None
    # Madrid capital district (folded key of extract.address.MADRID_DISTRICTS), when named.
    district: str | None = None

    def chat_text(self) -> str:
        labels = {"advertiser": "ANUNCIANTE", "user": "USUARIO", "unknown": "?"}
        return "\n".join(f"{labels[m.sender]}: {m.text}" for m in self.chat)

    def all_text(self) -> str:
        """All free text, for deterministic pattern checks."""
        parts = [self.listing_text, self.pasted_text, self.chat_text()]
        return "\n\n".join(p for p in parts if p.strip())

    def has_material(self) -> bool:
        return bool(self.all_text().strip() or self.ibans or self.phones)


class Signal(BaseModel):
    """One reason behind a vote, in plain Spanish for the report."""

    code: str
    # Contribution to risk: positive = scam-like, negative = reassuring.
    weight: float
    message: str
    quote: str | None = None
    advice: str | None = None
    # English of a message built with case data; without it an English report uses the
    # code's generic line (rubric_en.POINTS_EN).
    message_en: str | None = None


class Vote(BaseModel):
    voter: str
    # -1 (clearly legitimate) .. +1 (clearly scam-like). 0 with confidence 0 = abstain.
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    hard_flag: bool = False
    signals: list[Signal] = Field(default_factory=list)

    @classmethod
    def abstain(cls, voter: str, reason: str | None = None) -> Vote:
        return cls(voter=voter, score=0.0, confidence=0.0, evidence=[reason] if reason else [])


class HardRule(BaseModel):
    code: str
    # Minimum level the verdict can have once this rule fires.
    floor: Level
    # A locked rule cannot be softened by the adversarial step.
    locked: bool
    message: str


class Verdict(BaseModel):
    level: Level
    confidence: float = Field(ge=0.0, le=1.0)
    score: float
    signals: list[Signal] = Field(default_factory=list)
    hard_rules: list[HardRule] = Field(default_factory=list)
    insufficient_info: bool = False
    injection_detected: bool = False
    adversarial_notes: list[str] = Field(default_factory=list)
    softened_by_adversary: bool = False

    @property
    def locked(self) -> bool:
        return any(r.locked and r.floor == self.level for r in self.hard_rules)
