"""Street addresses in Spanish free text, normalised for matching across cases.

A property's address is the strongest link between a copied listing and the real one: scammers
republish a real flat (address and photos) under their own phone. Two keys per address:
- building: street type + name + number (+ municipality when known), floor and door dropped;
- street: street type + name (+ municipality), for weaker "same street" patterns.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_TYPES = {
    "calle": "calle", "c": "calle", "cl": "calle", "avenida": "avenida", "avda": "avenida",
    "av": "avenida", "plaza": "plaza", "pza": "plaza", "pl": "plaza", "paseo": "paseo",
    "po": "paseo", "camino": "camino", "travesia": "travesia", "trav": "travesia",
    "ronda": "ronda", "glorieta": "glorieta", "carretera": "carretera", "ctra": "carretera",
    "urbanizacion": "urbanizacion", "urb": "urbanizacion", "callejon": "callejon",
    "costanilla": "costanilla", "via": "via",
}  # fmt: skip
_CONNECTORS = {"de", "del", "la", "las", "el", "los", "y"}
# Municipalities: the Corredor del Henares and the Comunidad de Madrid towns the market mirror
# covers (or may cover). Folded name or unambiguous short form → canonical name. Short forms that
# are also common words or surnames (Pinto, Rivas, Villalba) are left out on purpose: a wrong
# town brings a wrong price reference.
_MUNICIPALITY_ES = {
    "alcala de henares": "Alcalá de Henares", "torrejon de ardoz": "Torrejón de Ardoz",
    "coslada": "Coslada", "san fernando de henares": "San Fernando de Henares", "meco": "Meco",
    "azuqueca de henares": "Azuqueca de Henares", "guadalajara": "Guadalajara",
    "madrid": "Madrid", "villalbilla": "Villalbilla", "los santos": "Los Santos de la Humosa",
    "camarma de esteruelas": "Camarma de Esteruelas", "mostoles": "Móstoles",
    "fuenlabrada": "Fuenlabrada", "leganes": "Leganés", "getafe": "Getafe",
    "alcorcon": "Alcorcón", "parla": "Parla", "alcobendas": "Alcobendas",
    "las rozas de madrid": "Las Rozas de Madrid",
    "san sebastian de los reyes": "San Sebastián de los Reyes",
    "pozuelo de alarcon": "Pozuelo de Alarcón", "rivas vaciamadrid": "Rivas-Vaciamadrid",
    "valdemoro": "Valdemoro", "majadahonda": "Majadahonda", "collado villalba": "Collado Villalba",
    "aranjuez": "Aranjuez", "arganda del rey": "Arganda del Rey",
    "boadilla del monte": "Boadilla del Monte", "pinto": "Pinto",
    "colmenar viejo": "Colmenar Viejo",
    "tres cantos": "Tres Cantos", "galapagar": "Galapagar", "arroyomolinos": "Arroyomolinos",
    "villaviciosa de odon": "Villaviciosa de Odón", "navalcarnero": "Navalcarnero",
    "ciempozuelos": "Ciempozuelos", "torrelodones": "Torrelodones",
    "paracuellos de jarama": "Paracuellos de Jarama", "mejorada del campo": "Mejorada del Campo",
    "algete": "Algete", "san martin de la vega": "San Martín de la Vega",
    "humanes de madrid": "Humanes de Madrid", "velilla de san antonio": "Velilla de San Antonio",
    "daganzo de arriba": "Daganzo de Arriba", "ajalvir": "Ajalvir", "loeches": "Loeches",
    "torres de la alameda": "Torres de la Alameda",
    "villanueva de la canada": "Villanueva de la Cañada",
}  # fmt: skip
_MUNICIPALITIES = {
    **{k: k for k in _MUNICIPALITY_ES if k not in ("los santos", "pinto")},
    "alcala": "alcala de henares", "torrejon": "torrejon de ardoz",
    "los santos de la humosa": "los santos", "las rozas": "las rozas de madrid",
    "rivas-vaciamadrid": "rivas vaciamadrid", "pozuelo": "pozuelo de alarcon",
    "boadilla": "boadilla del monte", "arganda": "arganda del rey",
    "paracuellos": "paracuellos de jarama", "daganzo": "daganzo de arriba",
    "san sebastian de los reyes": "san sebastian de los reyes",
    # Pinto only with its province or "en Pinto (Madrid)": "pinto" alone is also a verb.
    "pinto, madrid": "pinto", "pinto (madrid)": "pinto",
}  # fmt: skip

# How to write them for the user.
MUNICIPALITY_ES = _MUNICIPALITY_ES

# Madrid capital's 21 districts (folded → how to write them). The market mirror sweeps each one
# as its own zone (madrid/<district>); a price reference needs to know which.
MADRID_DISTRICTS = {
    "centro": "Centro", "arganzuela": "Arganzuela", "retiro": "Retiro",
    "salamanca": "Salamanca", "chamartin": "Chamartín", "tetuan": "Tetuán",
    "chamberi": "Chamberí", "fuencarral el pardo": "Fuencarral-El Pardo",
    "moncloa aravaca": "Moncloa-Aravaca", "latina": "Latina", "carabanchel": "Carabanchel",
    "usera": "Usera", "puente de vallecas": "Puente de Vallecas", "moratalaz": "Moratalaz",
    "ciudad lineal": "Ciudad Lineal", "hortaleza": "Hortaleza", "villaverde": "Villaverde",
    "villa de vallecas": "Villa de Vallecas", "vicalvaro": "Vicálvaro",
    "san blas canillejas": "San Blas-Canillejas", "barajas": "Barajas",
}  # fmt: skip
_DISTRICT_ALIASES = {
    **{k: k for k in MADRID_DISTRICTS if k not in ("centro", "latina", "retiro", "salamanca")},
    "fuencarral": "fuencarral el pardo", "moncloa": "moncloa aravaca",
    "san blas": "san blas canillejas",
    # Words that are also ordinary ones only with "barrio"/"distrito" in front.
    "distrito centro": "centro", "barrio de salamanca": "salamanca",
    "barrio salamanca": "salamanca",
    "distrito salamanca": "salamanca", "distrito de salamanca": "salamanca",
    "barrio del retiro": "retiro", "distrito retiro": "retiro", "distrito de retiro": "retiro",
    "barrio de la latina": "latina", "la latina": "latina", "distrito latina": "latina",
    # Well-known neighbourhoods that belong to a single district (none that is also a common
    # word or shared between two, like "Vallecas" or "Cuatro Caminos").
    "lavapies": "centro", "malasana": "centro", "chueca": "centro", "embajadores": "centro",
    "arguelles": "moncloa aravaca", "aravaca": "moncloa aravaca", "legazpi": "arganzuela",
    "aluche": "latina", "sanchinarro": "hortaleza", "valdebebas": "hortaleza",
    "las tablas": "fuencarral el pardo", "montecarmelo": "fuencarral el pardo",
    "mirasierra": "fuencarral el pardo", "ensanche de vallecas": "villa de vallecas",
    "pau de vallecas": "villa de vallecas", "valdebernardo": "vicalvaro",
    "guindalera": "salamanca", "pinar de chamartin": "chamartin", "opanel": "carabanchel",
    "canillejas": "san blas canillejas", "pueblo nuevo": "ciudad lineal",
}  # fmt: skip

_TYPE_RE = (
    r"(?:calle|c/|c\.|cl\.|avenida|avda\.?|av\.|plaza|pza\.?|pl\.|paseo|p[ºo°]\.?|camino|"
    r"traves[ií]a|trav\.|ronda|glorieta|carretera|ctra\.?|urbanizaci[oó]n|urb\.|callej[oó]n|"
    r"costanilla|v[ií]a)"
)
# A street name never runs on through these: "calle Libreros en Alcalá, 2 habitaciones" is
# calle Libreros with no number (Telegram test 2026-10-08).
_NOT_NAME = r"(?!(?:en|por|con|cerca|junto|para|desde|hasta)\b)"
# A count of something is not a house number: "calle Mayor, 2 habitaciones".
_NOT_NUMBER = (
    r"(?!\d|\s*(?:hab|dorm|m2|m²|metros|€|eur|personas|chicas|chicos|mes|años|días|dias|min|km)"
    r")"
)
# Type, then 1-6 name words (letters, possibly with connectors), then a number.
_ADDRESS_RE = re.compile(
    _TYPE_RE
    + r"\s*((?:"
    + _NOT_NAME
    + r"[^\W\d_][\w'’.-]*\s+){0,5}?"
    + _NOT_NAME
    + r"[^\W\d_][\w'’.-]*)\s*,?\s*(?:n[ºo°.]?\s*)?"
    r"(\d{1,4})"
    + _NOT_NUMBER
    + r"(?:\s*,?\s*((?:\d{1,2}\s*[ºª°]?\s*[a-z]?\b|bajo|bj|ático|atico)[\w\s-]{0,6}))?",
    re.I,
)


@dataclass(frozen=True)
class Address:
    raw: str
    street_type: str
    street: str  # normalised name, connectors removed
    number: str
    municipality: str | None

    @property
    def building_key(self) -> str:
        return f"{self.street_type} {self.street} {self.number}|{self.municipality or '?'}"

    @property
    def street_key(self) -> str:
        return f"{self.street_type} {self.street}|{self.municipality or '?'}"


def street_of(building_key: str) -> str:
    """'calle colegios 14|alcala de henares' -> 'calle colegios|alcala de henares'."""
    building, _, town = building_key.partition("|")
    return f"{building.rsplit(' ', 1)[0]}|{town}"


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


# "calle Alcalá", "avenida de Guadalajara": a street named after a town, number or not.
_STREET_BEFORE = re.compile(
    r"(?:calle|c/|c\.|avenida|avda\.?|av\.|plaza|paseo|camino|ronda|carretera|ctra\.?|"
    r"glorieta|traves[ií]a)\s+(?:(?:de|del)\s+)?(?:(?:la|los|las|el)\s+)?$"
)


def _first(folded: str, names: dict[str, str]) -> str | None:
    for name in sorted(names, key=len, reverse=True):
        for m in re.finditer(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", folded):
            if not _STREET_BEFORE.search(folded[max(0, m.start() - 30) : m.start()]):
                return names[name]
    return None


_NOT_CAPITAL = {k: v for k, v in _MUNICIPALITIES.items() if v != "madrid"}


def municipality_in(text: str) -> str | None:
    """Any other town before Madrid: "Getafe, Madrid" and "Comunidad de Madrid" are not the
    capital."""
    folded = re.sub(r"comunidad (?:autonoma )?de madrid", " ", _fold(text))
    return _first(folded, _NOT_CAPITAL) or _first(folded, _MUNICIPALITIES)


# Spanish cities outside the Comunidad (province capitals and big towns, folded). Only used to
# stop a Madrid-sounding neighbourhood from making a case Madrid capital: "piso en Barcelona,
# barrio Pueblo Nuevo" is Poblenou, not Ciudad Lineal. Left out on purpose: León (calle Ponce de
# León), Salamanca (a Madrid district) and San Sebastián (San Sebastián de los Reyes is in the
# Comunidad: Donostia).
_ELSEWHERE = {
    name: name
    for name in (
        "a coruna", "la coruna", "albacete", "alicante", "almeria", "avila", "badajoz",
        "barcelona", "bilbao", "burgos", "caceres", "cadiz", "castellon", "ciudad real",
        "cordoba", "cuenca", "girona", "gerona", "granada", "huelva", "huesca", "jaen",
        "lleida", "lerida", "logrono", "lugo", "malaga", "murcia", "ourense", "orense",
        "oviedo", "palencia", "palma de mallorca", "pamplona", "pontevedra", "donostia",
        "santander", "segovia", "sevilla", "soria", "tarragona", "teruel", "toledo",
        "valencia", "valladolid", "vitoria", "zamora", "zaragoza", "ceuta", "melilla",
        "tenerife", "las palmas", "gijon", "vigo", "elche", "cartagena", "marbella",
        "santiago de compostela", "salou", "benidorm", "torremolinos", "badalona",
        "hospitalet", "sabadell", "terrassa", "ibiza", "menorca", "mallorca",
    )
}  # fmt: skip


def elsewhere_in(text: str) -> str | None:
    """A Spanish city outside the Comunidad de Madrid named in the text, outside street names
    ("Ronda de Valencia" is in Madrid)."""
    return _first(_fold(text), _ELSEWHERE)


def district_in(text: str) -> str | None:
    """Madrid capital district named in the text (folded key of MADRID_DISTRICTS), outside
    street names. Only meaningful when the town is Madrid."""
    blanked = text
    for m in _ADDRESS_RE.finditer(text):
        blanked = blanked[: m.start()] + " " * (m.end() - m.start()) + blanked[m.end() :]
    return _first(_fold(blanked).replace("-", " "), _DISTRICT_ALIASES)


def town_of(text: str) -> str | None:
    """Municipality named in the text outside street names ("Avenida de Guadalajara" is not
    Guadalajara)."""
    blanked = text
    for m in _ADDRESS_RE.finditer(text):
        blanked = blanked[: m.start()] + " " * (m.end() - m.start()) + blanked[m.end() :]
    return municipality_in(blanked)


def find_addresses(text: str, *, default_municipality: str | None = None) -> list[Address]:
    """Addresses in `text`. The municipality comes from the text near the address, else from
    anywhere in the text, else the default."""
    out: list[Address] = []
    matches = list(_ADDRESS_RE.finditer(text))
    # The town comes from outside the addresses: "Avenida de Guadalajara 35" is in Alcalá.
    blanked = text
    for m in matches:
        blanked = blanked[: m.start()] + " " * (m.end() - m.start()) + blanked[m.end() :]
    whole_town = municipality_in(blanked) or default_municipality
    for m in matches:
        raw_type = _fold(m.group(0).split()[0]).rstrip("./")
        street_type = _TYPES.get(re.sub(r"[^a-z]", "", raw_type))
        if not street_type:
            continue
        words = [w for w in re.findall(r"[a-z0-9']+", _fold(m.group(1))) if w not in _CONNECTORS]
        if not words or len("".join(words)) < 3:
            continue
        near = municipality_in(blanked[m.end() : m.end() + 60])
        out.append(
            Address(
                raw=m.group(0).strip(" ,."),
                street_type=street_type,
                street=" ".join(words),
                number=str(int(m.group(2))),
                municipality=near or whole_town,
            )
        )
    seen, unique = set(), []
    for a in out:
        if a.building_key not in seen:
            seen.add(a.building_key)
            unique.append(a)
    return unique
