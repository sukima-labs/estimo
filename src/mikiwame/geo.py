"""Does a street exist in a municipality? OpenStreetMap's Nominatim, politely.

OSM has few house numbers in the area (01-10-2026: "Calle de los Colegios 14" comes back as the
street, and so does "Calle Mayor 400"), so only the street is checked and only the street and
the municipality leave the house: never the number, floor or door.

Nominatim's usage policy: at most 1 request per second, our own User-Agent, and cache results.
Answers are cached under an HMAC of the street key (a street a user is looking at is still a
hint about them).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import httpx
from sqlalchemy import delete, insert, select

from mikiwame import USER_AGENT
from mikiwame.indicators import digest
from mikiwame.store import schema
from mikiwame.store.sql import Store, utcnow

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
CACHE_DAYS = 30
MIN_INTERVAL_S = 1.1


class StreetChecker:
    def __init__(
        self,
        store: Store,
        key: bytes,
        *,
        base_url: str = NOMINATIM_URL,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 8.0,
    ) -> None:
        self.store = store
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": f"{USER_AGENT} (rental-scam checker)"}
        )
        self._lock = asyncio.Lock()
        self._last = -1e9

    async def exists(self, street: str, municipality: str) -> bool | None:
        """True/False, or None when it could not be checked (network, rate limit, bad answer).
        `street` is our normalised form ("calle colegios"); if it is not found with its street
        type, the bare name is tried: "Calle Guadalajara" for an avenida is a slip, not a lie."""
        cache_key = digest(self.key, "street", f"{street}|{municipality}")
        cached = self._cached(cache_key)
        if cached is not None:
            return cached
        found = await self._search(street, municipality)
        if found is False and " " in street:
            found = await self._search(street.split(" ", 1)[1], municipality)
        if found is not None:
            self._store(cache_key, found)
        return found

    async def _search(self, street: str, municipality: str) -> bool | None:
        async with self._lock:
            wait = self._last + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                r = await self.client.get(
                    f"{self.base_url}/search",
                    params={
                        "street": street,
                        "city": municipality,
                        "countrycodes": "es",
                        "format": "jsonv2",
                        "limit": 1,
                    },
                )
            except httpx.HTTPError as e:
                log.warning("nominatim unreachable: %s", type(e).__name__)
                return None
            finally:
                self._last = time.monotonic()
        if r.status_code != 200:
            log.warning("nominatim answered HTTP %s", r.status_code)
            return None
        try:
            results = r.json()
        except ValueError:
            return None
        return bool(results) if isinstance(results, list) else None

    def _cached(self, key: str) -> bool | None:
        t = schema.street_checks
        since = utcnow() - dt.timedelta(days=CACHE_DAYS)
        with self.store.engine.connect() as conn:
            row = conn.execute(
                select(t.c.found).where(t.c.key_hmac == key, t.c.checked_at >= since)
            ).first()
        return None if row is None else bool(row.found)

    def _store(self, key: str, found: bool) -> None:
        t = schema.street_checks
        with self.store.engine.begin() as conn:
            conn.execute(delete(t).where(t.c.key_hmac == key))
            conn.execute(insert(t).values(key_hmac=key, found=found, checked_at=utcnow()))

    async def aclose(self) -> None:
        await self.client.aclose()
