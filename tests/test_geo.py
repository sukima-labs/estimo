from __future__ import annotations

import httpx
import pytest
import respx

from mikiwame import geo
from mikiwame.extract.build import enrich
from mikiwame.geo import StreetChecker
from mikiwame.models import Case, Message
from mikiwame.store.sql import Store
from mikiwame.voters.address import AddressVoter

URL = "https://nominatim.test/search"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(geo, "MIN_INTERVAL_S", 0.0)


@pytest.fixture
def checker(tmp_path):
    return StreetChecker(
        Store(f"sqlite:///{tmp_path / 'g.db'}"), b"k" * 32, base_url="https://nominatim.test"
    )


def _case(text: str) -> Case:
    return enrich(Case(chat=[Message(sender="advertiser", text=text)]))


@respx.mock
async def test_street_found_and_cached(checker):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[{"place_id": 1}]))
    assert await checker.exists("calle colegios", "alcala de henares") is True
    assert await checker.exists("calle colegios", "alcala de henares") is True
    assert route.call_count == 1
    sent = route.calls[0].request.url.params
    assert sent["street"] == "calle colegios" and sent["city"] == "alcala de henares"
    assert route.calls[0].request.headers["User-Agent"].startswith("mikiwame/")


@respx.mock
async def test_wrong_street_type_is_not_a_missing_street(checker):
    def answer(request):
        found = request.url.params["street"] == "guadalajara"
        return httpx.Response(200, json=[{"place_id": 1}] if found else [])

    respx.get(URL).mock(side_effect=answer)
    assert await checker.exists("calle guadalajara", "alcala de henares") is True


@respx.mock
async def test_unreachable_is_unknown_and_not_cached(checker):
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    assert await checker.exists("calle colegios", "alcala de henares") is None
    route.mock(return_value=httpx.Response(429))
    assert await checker.exists("calle colegios", "alcala de henares") is None
    route.mock(return_value=httpx.Response(200, json=[]))
    assert await checker.exists("calle colegios", "alcala de henares") is False


@respx.mock
async def test_address_voter_flags_a_missing_street_without_sending_the_number(checker):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    case = _case("El piso está en la Calle Inventada Falsa 3, 2ºB, Alcalá de Henares.")
    vote = await AddressVoter(checker).vote(case)
    assert [s.code for s in vote.signals] == ["street_not_found"]
    assert "Calle Inventada Falsa" in vote.signals[0].message
    assert "Alcalá de Henares" in vote.signals[0].message
    assert not vote.hard_flag and 0 < vote.score < 0.2
    for call in route.calls:
        assert "3" not in str(call.request.url.params)


@respx.mock
async def test_address_voter_abstains_without_town_or_on_errors(checker):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    no_town = Case(addresses=["calle mayor 5|?"])
    assert (await AddressVoter(checker).vote(no_town)).confidence == 0
    assert route.call_count == 0
    route.mock(side_effect=httpx.ConnectError("down"))
    case = _case("Calle de los Colegios 14, Alcalá de Henares")
    assert (await AddressVoter(checker).vote(case)).confidence == 0
    assert (await AddressVoter(None).vote(case)).confidence == 0


async def test_address_voter_gives_up_after_its_timeout(monkeypatch):
    import asyncio

    from mikiwame.voters import address

    class Hung:
        async def exists(self, street, town):
            await asyncio.sleep(5)

    monkeypatch.setattr(address, "TIMEOUT_S", 0.05)
    case = _case("Calle de los Colegios 14, Alcalá de Henares")
    assert (await AddressVoter(Hung()).vote(case)).confidence == 0
