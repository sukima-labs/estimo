"""Market database: a local mirror of ct/128's rental listings (verb `alquileres`).

Shared by every bot instance (it is knowledge about the market, not about our users), kept apart
from the per-instance case databases. Particular
advertisers' names are stored only as the same keyed HMAC the case registry uses; agency names
are kept in clear (a business name is not personal data).
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    case,
    func,
    select,
)
from sqlalchemy.dialects import postgresql, sqlite

from mikiwame.store.schema import UTCDateTime
from mikiwame.store.sql import add_missing_columns, make_engine, utcnow

metadata = MetaData()

listings = Table(
    "listings",
    metadata,
    Column("source", String(16), primary_key=True),
    Column("listing_id", String(32), primary_key=True),
    Column("url", Text),
    Column("category", String(16), index=True),
    Column("type", String(16)),
    Column("price_eur", Integer),
    Column("price_history", Text),  # JSON [{seen_at, price_eur}]
    Column("size_m2", Float),
    Column("rooms", Integer),
    Column("municipality", String(64), index=True),
    Column("neighborhood", String(64)),
    Column("first_seen", UTCDateTime),
    Column("last_seen", UTCDateTime),
    Column("removed_at", UTCDateTime),
    Column("status", String(16)),
    Column("last_sweep_at", String(10)),
    Column("advertiser_type", String(16)),
    Column("advertiser_business", Text),  # agency name in clear
    Column("advertiser_hmac", String(64), index=True),  # HMAC of the normalised name
    Column("photo_url", Text),
    Column("description_snippet", Text),
    Column("synced_at", UTCDateTime, nullable=False),
    # Schema 2: times the portal marked it removed and then saw it again (false removals).
    Column("reappeared_count", Integer),
)

# Schema 2 sweep lines: which UTC days each (zone, grid) was looked at, and which of them were a
# complete enumeration whose absences were acted on. Only complete days make "missing" mean
# anything; madrid/centro never is (60-page cap).
sweeps = Table(
    "sweeps",
    metadata,
    Column("municipality", String(64), primary_key=True),
    Column("category", String(16), primary_key=True),
    Column("day", String(10), primary_key=True),
    Column("complete", Boolean, nullable=False),
)

sync_log = Table(
    "sync_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("started_at", UTCDateTime, nullable=False),
    Column("since", String(32), nullable=False),
    Column("rc", Integer, nullable=False),
    Column("lines", Integer, nullable=False),
    Column("upserted", Integer, nullable=False),
    Column("error", Text),
)

# Manual reviews of listings. One row per listing;
# the note is short and carries no personal data (no names, phones or IBANs).
radar_reviews = Table(
    "radar_reviews",
    metadata,
    Column("source", String(16), primary_key=True),
    Column("listing_id", String(32), primary_key=True),
    Column("reviewed_at", UTCDateTime, nullable=False),
    Column("outcome", String(24), nullable=False),
    Column("note", Text),
)


def _ts(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


@dataclass(frozen=True)
class MarketListing:
    source: str
    listing_id: str
    category: str | None
    price_eur: int | None
    price_history: list[dict]
    size_m2: float | None
    first_seen: dt.datetime | None
    last_seen: dt.datetime | None
    removed_at: dt.datetime | None
    status: str | None
    advertiser_type: str | None
    advertiser_business: str | None
    advertiser_hmac: str | None
    municipality: str | None
    reappeared_count: int | None = None
    rooms: int | None = None


@dataclass(frozen=True)
class SweepState:
    last_swept: dt.date
    last_complete: dt.date | None


@dataclass(frozen=True)
class ZoneCoverage:
    municipality: str
    category: str
    listings: int
    removed: int
    last_swept: dt.date | None
    last_complete: dt.date | None
    # Listings without a removal that carry a neighbourhood (idealista fills it from 2026-10-14).
    with_neighborhood: int = 0


def _day(t: dt.datetime) -> dt.date:
    return (t if t.tzinfo else t.replace(tzinfo=dt.UTC)).astimezone(dt.UTC).date()


def legacy_complete(municipality: str) -> bool:
    """Before schema 2 sweep lines: the mirror held only Alcalá, whose sweeps are complete."""
    return not municipality.startswith("madrid/centro")


class MarketStore:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        metadata.create_all(self.engine)
        add_missing_columns(self.engine, metadata)

    def upsert_sweeps(self, rows: Iterable[dict]) -> int:
        """rows: {municipality, category, day, complete}. A day once complete stays complete."""
        n = 0
        insert = postgresql.insert if self.engine.dialect.name == "postgresql" else sqlite.insert
        with self.engine.begin() as conn:
            for r in rows:
                stmt = insert(sweeps).values(**r)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["municipality", "category", "day"],
                    set_={"complete": sweeps.c.complete | stmt.excluded.complete},
                )
                conn.execute(stmt)
                n += 1
        return n

    def has_sweeps(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(sweeps.c.day).limit(1)).first() is not None

    def sweep_state(self, municipality: str | None, category: str | None) -> SweepState | None:
        """Latest swept and latest complete day of a (zone, grid); None before any sweep line.

        Completeness is per day, not per zone (idealista, 03-10-2026): madrid/chamberi was
        complete on 02-09 and 05-09 and cut short on 09-09 and 03-10. status and removed_at are
        written only on complete days, so they hold as of last_complete; after that, only "seen
        in that sweep" (last_seen against last_swept) says anything."""
        if not municipality:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                select(
                    func.max(sweeps.c.day),
                    func.max(case((sweeps.c.complete, sweeps.c.day), else_=None)),
                ).where(sweeps.c.municipality == municipality, sweeps.c.category == category)
            ).first()
        if not row or not row[0]:
            return None
        return SweepState(
            last_swept=dt.date.fromisoformat(row[0]),
            last_complete=dt.date.fromisoformat(row[1]) if row[1] else None,
        )

    def presence(self, m: MarketListing) -> str:
        """What we can say about a listing now: "removed", "active" or "unknown"."""
        if m.removed_at:
            return "removed"
        state = self.sweep_state(m.municipality, m.category)
        if state is None:  # before schema 2 sweep lines
            ok = m.status == "active" and legacy_complete(m.municipality or "")
            return "active" if ok else "unknown"
        if m.last_seen and _day(m.last_seen) >= state.last_swept:
            return "active"  # seen in the latest sweep, complete or not
        fresh = state.last_complete is not None and state.last_complete >= state.last_swept
        return "active" if m.status == "active" and fresh else "unknown"

    def coverage(self) -> list[ZoneCoverage]:
        """Per (zone, grid): listings it holds and how fresh its sweeps are, for stats and
        the Monday digest. Sweep days are None before schema 2 sweep lines."""
        with self.engine.connect() as conn:
            counts = conn.execute(
                select(
                    listings.c.municipality,
                    listings.c.category,
                    func.count(),
                    func.sum(case((listings.c.removed_at.is_not(None), 1), else_=0)),
                    func.sum(
                        case(
                            (
                                listings.c.removed_at.is_(None)
                                & (func.coalesce(listings.c.neighborhood, "") != ""),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                )
                .where(listings.c.municipality.is_not(None), listings.c.category.is_not(None))
                .group_by(listings.c.municipality, listings.c.category)
            ).all()
            days = conn.execute(
                select(
                    sweeps.c.municipality,
                    sweeps.c.category,
                    func.max(sweeps.c.day),
                    func.max(case((sweeps.c.complete, sweeps.c.day), else_=None)),
                ).group_by(sweeps.c.municipality, sweeps.c.category)
            ).all()
        out: dict[tuple[str, str], ZoneCoverage] = {}
        for muni, cat, n, removed, barrio in counts:
            out[(muni, cat)] = ZoneCoverage(
                muni, cat, n, int(removed or 0), None, None, int(barrio or 0)
            )
        for muni, cat, last, complete in days:
            prev = out.get((muni, cat)) or ZoneCoverage(muni, cat, 0, 0, None, None)
            out[(muni, cat)] = ZoneCoverage(
                muni, cat, prev.listings, prev.removed,
                dt.date.fromisoformat(last) if last else None,
                dt.date.fromisoformat(complete) if complete else None,
                prev.with_neighborhood,
            )  # fmt: skip
        return [out[k] for k in sorted(out)]

    def zones(self, category: str) -> list[str]:
        """Zone slugs (municipality values) that hold listings of this category."""
        with self.engine.connect() as conn:
            q = select(listings.c.municipality).where(listings.c.category == category).distinct()
            return [r[0] for r in conn.execute(q) if r[0]]

    def upsert(self, rows: Iterable[dict]) -> int:
        n = 0
        now = utcnow()
        insert = postgresql.insert if self.engine.dialect.name == "postgresql" else sqlite.insert
        with self.engine.begin() as conn:
            for r in rows:
                values = {**r, "synced_at": now}
                stmt = insert(listings).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["source", "listing_id"],
                    set_={k: v for k, v in values.items() if k not in ("source", "listing_id")},
                )
                conn.execute(stmt)
                n += 1
        return n

    def log_sync(self, *, started_at, since: str, rc: int, lines: int, upserted: int, error=None):
        with self.engine.begin() as conn:
            conn.execute(
                sync_log.insert().values(
                    started_at=started_at, since=since, rc=rc, lines=lines, upserted=upserted,
                    error=error,
                )
            )  # fmt: skip

    def last_good_sync_since(self) -> dt.datetime | None:
        """Latest last_seen we hold: the next sync asks from there (minus a margin)."""
        with self.engine.connect() as conn:
            v = conn.execute(select(func.max(listings.c.last_seen))).scalar()
        return v.replace(tzinfo=dt.UTC) if v is not None and v.tzinfo is None else v

    def get(self, source: str, listing_id: str) -> MarketListing | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(listings).where(
                    listings.c.source == source, listings.c.listing_id == listing_id
                )
            ).first()
        if r is None:
            return None
        m = r._mapping
        return MarketListing(
            source=m["source"],
            listing_id=m["listing_id"],
            category=m["category"],
            price_eur=m["price_eur"],
            price_history=json.loads(m["price_history"] or "[]"),
            size_m2=m["size_m2"],
            first_seen=m["first_seen"],
            last_seen=m["last_seen"],
            removed_at=m["removed_at"],
            status=m["status"],
            advertiser_type=m["advertiser_type"],
            advertiser_business=m["advertiser_business"],
            advertiser_hmac=m["advertiser_hmac"],
            municipality=m["municipality"],
            reappeared_count=m["reappeared_count"],
            rooms=m["rooms"],
        )

    def reference(
        self, municipality: str, category: str, rooms: int | None, *, min_n: int = 20
    ) -> dict | None:
        """Median of the last seen price of every listing (active or removed) of the same
        category, and of the same number of rooms (4 = 4 or more) when there are at least
        `min_n` of them; else of the whole category. None with fewer than `min_n` overall."""
        q = select(listings.c.price_eur, listings.c.rooms).where(
            listings.c.municipality == municipality,
            listings.c.category == category,
            listings.c.price_eur.is_not(None),
        )
        with self.engine.connect() as conn:
            rows = conn.execute(q).all()
        if rooms is not None:
            same = [r.price_eur for r in rows if r.rooms and min(r.rooms, 4) == min(rooms, 4)]
            if len(same) >= min_n:
                return {"median": statistics.median(same), "n": len(same), "rooms": rooms}
        allp = [r.price_eur for r in rows]
        if len(allp) >= min_n:
            return {"median": statistics.median(allp), "n": len(allp), "rooms": None}
        return None

    def price_stats(self, municipality: str, category: str) -> dict | None:
        """Median and spread of current prices, with sample size. None when too few.

        "Current" is status active when the latest sweep was complete; after a cut-short sweep,
        or where sweeps never are (madrid/centro), it is "seen in the latest sweep" instead."""
        cond = [
            listings.c.municipality == municipality,
            listings.c.category == category,
            listings.c.price_eur.is_not(None),
        ]
        state = self.sweep_state(municipality, category)
        if state is None or (state.last_complete and state.last_complete >= state.last_swept):
            cond.append(listings.c.status == "active")
        else:
            start = dt.datetime.combine(state.last_swept, dt.time(), tzinfo=dt.UTC)
            cond.append(listings.c.last_seen >= start)
        q = select(listings.c.price_eur, listings.c.size_m2).where(*cond)
        with self.engine.connect() as conn:
            rows = conn.execute(q).all()
        prices = sorted(r.price_eur for r in rows)
        if len(prices) < 10:
            return None
        per_m2 = sorted(r.price_eur / r.size_m2 for r in rows if r.size_m2)
        return {
            "n": len(prices),
            "median": statistics.median(prices),
            "p10": prices[len(prices) // 10],
            "p90": prices[(len(prices) * 9) // 10],
            "median_eur_m2": statistics.median(per_m2) if len(per_m2) >= 10 else None,
            "n_m2": len(per_m2),
        }
