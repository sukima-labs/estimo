"""Load the synthetic cases in tests/fixtures/cases/*.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from mikiwame import PROJECT_DIR

FIXTURES_DIR = PROJECT_DIR / "tests" / "fixtures"
CASES_DIR = FIXTURES_DIR / "cases"
IMAGES_DIR = FIXTURES_DIR / "images"


@dataclass(frozen=True)
class Fixture:
    id: str
    label: str  # scam | legit
    expect: tuple[str, ...]  # acceptable levels, ideal first
    kind: str
    notes: str
    listing_url: str
    listing_text: str
    advertiser_name: str
    advertiser_phone: str
    chat: tuple[tuple[str, str], ...]  # (who, text); who is "me" or "them"
    pasted: str = ""
    # Where the chat happens. Rendered screenshots look like WhatsApp, hence the default.
    channel: str = "whatsapp"
    # Signal codes that must (and must not) show up in the verdict: measured, not gating.
    expect_signals: tuple[str, ...] = ()
    absent_signals: tuple[str, ...] = ()
    path: Path = field(default=Path(), compare=False)

    @property
    def is_scam(self) -> bool:
        return self.label == "scam"

    def image_paths(self) -> list[Path]:
        return sorted(IMAGES_DIR.glob(f"{self.id}_*.jpg"))


def load(path: Path) -> Fixture:
    d = tomllib.loads(path.read_text(encoding="utf-8"))
    listing = d.get("listing", {})
    adv = d.get("advertiser", {})
    fx = Fixture(
        id=d["id"],
        label=d["label"],
        expect=tuple(d["expect"]),
        kind=d.get("kind", "unknown"),
        notes=d.get("notes", ""),
        listing_url=listing.get("url", ""),
        listing_text=listing.get("text", "").strip(),
        advertiser_name=adv.get("name", ""),
        advertiser_phone=adv.get("phone", ""),
        chat=tuple((who, text) for who, text in d.get("chat", [])),
        pasted=d.get("pasted", ""),
        channel=d.get("channel", "whatsapp" if d.get("chat") else "unknown"),
        expect_signals=tuple(d.get("expect_signals", ())),
        absent_signals=tuple(d.get("absent_signals", ())),
        path=path,
    )
    if fx.label not in ("scam", "legit"):
        raise ValueError(f"{path}: bad label {fx.label!r}")
    if not set(fx.expect) <= {"green", "amber", "red"}:
        raise ValueError(f"{path}: bad expect {fx.expect!r}")
    if fx.label == "legit" and "red" in fx.expect:
        raise ValueError(f"{path}: a legit fixture can never accept red")
    return fx


def load_all(pattern: str = "*") -> list[Fixture]:
    return [load(p) for p in sorted(CASES_DIR.glob(f"{pattern}.toml"))]


def oracle_case(fx: Fixture):
    """The Case a perfect screenshot reader would produce, through the same enrichment."""
    from mikiwame.extract.build import enrich
    from mikiwame.models import Case, Message

    case = Case(
        case_id=f"fx-{fx.id}",
        listing_url=fx.listing_url or None,
        listing_text=fx.listing_text,
        pasted_text=fx.pasted,
        chat=[
            Message(sender="advertiser" if who == "them" else "user", text=text)
            for who, text in fx.chat
        ],
        advertiser_names=[fx.advertiser_name] if fx.advertiser_name else [],
        phones=[fx.advertiser_phone] if fx.advertiser_phone else [],
        advertiser_phones=[fx.advertiser_phone] if fx.advertiser_phone else [],
        channel=fx.channel,
        property_type="habitacion" if fx.kind == "habitacion" else "piso",
        id_documents=sum("[Imagen: DNI" in text for who, text in fx.chat if who == "them"),
    )
    return enrich(case)
