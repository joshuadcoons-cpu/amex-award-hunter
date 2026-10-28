"""Airport reference data: U.S. origins and a maintainable European destination set.

Extend ``EUROPEAN_AIRPORTS`` to add destinations; the search page lets you enable
any subset. Country names drive the "excluded destination countries" filter.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Airport:
    code: str
    city: str
    country: str
    name: str
    metro: str | None = None  # shared metro key for open-jaw "same city" logic


US_ORIGINS: dict[str, Airport] = {
    "SAN": Airport("SAN", "San Diego", "United States", "San Diego International", "SAN"),
    "LAX": Airport("LAX", "Los Angeles", "United States", "Los Angeles International", "LAX"),
    "SFO": Airport("SFO", "San Francisco", "United States", "San Francisco International", "SFO"),
    "SEA": Airport("SEA", "Seattle", "United States", "Seattle-Tacoma International", "SEA"),
    "SJC": Airport("SJC", "San Jose", "United States", "San Jose Mineta International", "SFO"),
    "OAK": Airport("OAK", "Oakland", "United States", "Oakland International", "SFO"),
    "SNA": Airport("SNA", "Orange County", "United States", "John Wayne Airport", "LAX"),
    "ONT": Airport("ONT", "Ontario", "United States", "Ontario International", "LAX"),
    "BUR": Airport("BUR", "Burbank", "United States", "Hollywood Burbank", "LAX"),
    "LAS": Airport("LAS", "Las Vegas", "United States", "Harry Reid International", "LAS"),
    "PHX": Airport("PHX", "Phoenix", "United States", "Phoenix Sky Harbor", "PHX"),
    "PDX": Airport("PDX", "Portland", "United States", "Portland International", "PDX"),
    "DEN": Airport("DEN", "Denver", "United States", "Denver International", "DEN"),
    "SLC": Airport("SLC", "Salt Lake City", "United States", "Salt Lake City International", "SLC"),
    "YVR": Airport("YVR", "Vancouver", "Canada", "Vancouver International", "YVR"),
    "ORD": Airport("ORD", "Chicago", "United States", "O'Hare International", "CHI"),
    "DFW": Airport("DFW", "Dallas", "United States", "Dallas/Fort Worth", "DFW"),
    "IAH": Airport("IAH", "Houston", "United States", "George Bush Intercontinental", "HOU"),
    "JFK": Airport("JFK", "New York", "United States", "John F. Kennedy International", "NYC"),
    "EWR": Airport("EWR", "Newark", "United States", "Newark Liberty", "NYC"),
    "BOS": Airport("BOS", "Boston", "United States", "Logan International", "BOS"),
    "IAD": Airport("IAD", "Washington", "United States", "Dulles International", "WAS"),
}

DEFAULT_PRIMARY_ORIGINS = ["SAN", "LAX", "SFO"]
DEFAULT_SECONDARY_ORIGINS = ["SEA"]

EUROPEAN_AIRPORTS: dict[str, Airport] = {
    # Italy
    "FCO": Airport("FCO", "Rome", "Italy", "Rome Fiumicino", "ROM"),
    "MXP": Airport("MXP", "Milan", "Italy", "Milan Malpensa", "MIL"),
    "LIN": Airport("LIN", "Milan", "Italy", "Milan Linate", "MIL"),
    "VCE": Airport("VCE", "Venice", "Italy", "Venice Marco Polo", "VCE"),
    "FLR": Airport("FLR", "Florence", "Italy", "Florence Peretola", "FLR"),
    "NAP": Airport("NAP", "Naples", "Italy", "Naples International", "NAP"),
    "BLQ": Airport("BLQ", "Bologna", "Italy", "Bologna Guglielmo Marconi", "BLQ"),
    # Spain
    "MAD": Airport("MAD", "Madrid", "Spain", "Madrid Barajas", "MAD"),
    "BCN": Airport("BCN", "Barcelona", "Spain", "Barcelona El Prat", "BCN"),
    "AGP": Airport("AGP", "Malaga", "Spain", "Malaga Costa del Sol", "AGP"),
    "PMI": Airport("PMI", "Palma de Mallorca", "Spain", "Palma de Mallorca", "PMI"),
    # Portugal
    "LIS": Airport("LIS", "Lisbon", "Portugal", "Lisbon Humberto Delgado", "LIS"),
    "OPO": Airport("OPO", "Porto", "Portugal", "Porto Francisco Sa Carneiro", "OPO"),
    # Netherlands
    "AMS": Airport("AMS", "Amsterdam", "Netherlands", "Amsterdam Schiphol", "AMS"),
    # Switzerland
    "ZRH": Airport("ZRH", "Zurich", "Switzerland", "Zurich Airport", "ZRH"),
    "GVA": Airport("GVA", "Geneva", "Switzerland", "Geneva Airport", "GVA"),
    # Austria
    "VIE": Airport("VIE", "Vienna", "Austria", "Vienna International", "VIE"),
    # Germany
    "FRA": Airport("FRA", "Frankfurt", "Germany", "Frankfurt am Main", "FRA"),
    "MUC": Airport("MUC", "Munich", "Germany", "Munich Airport", "MUC"),
    "BER": Airport("BER", "Berlin", "Germany", "Berlin Brandenburg", "BER"),
    "DUS": Airport("DUS", "Dusseldorf", "Germany", "Dusseldorf Airport", "DUS"),
    "HAM": Airport("HAM", "Hamburg", "Germany", "Hamburg Airport", "HAM"),
    # United Kingdom
    "LHR": Airport("LHR", "London", "United Kingdom", "London Heathrow", "LON"),
    "LGW": Airport("LGW", "London", "United Kingdom", "London Gatwick", "LON"),
    "MAN": Airport("MAN", "Manchester", "United Kingdom", "Manchester Airport", "MAN"),
    "EDI": Airport("EDI", "Edinburgh", "United Kingdom", "Edinburgh Airport", "EDI"),
    # Ireland
    "DUB": Airport("DUB", "Dublin", "Ireland", "Dublin Airport", "DUB"),
    # Greece
    "ATH": Airport("ATH", "Athens", "Greece", "Athens Eleftherios Venizelos", "ATH"),
    # Denmark / Nordics
    "CPH": Airport("CPH", "Copenhagen", "Denmark", "Copenhagen Kastrup", "CPH"),
    "ARN": Airport("ARN", "Stockholm", "Sweden", "Stockholm Arlanda", "STO"),
    "OSL": Airport("OSL", "Oslo", "Norway", "Oslo Gardermoen", "OSL"),
    "HEL": Airport("HEL", "Helsinki", "Finland", "Helsinki-Vantaa", "HEL"),
    "KEF": Airport("KEF", "Reykjavik", "Iceland", "Keflavik International", "REK"),
    # Belgium
    "BRU": Airport("BRU", "Brussels", "Belgium", "Brussels Airport", "BRU"),
    # Czechia / Hungary / Poland
    "PRG": Airport("PRG", "Prague", "Czechia", "Vaclav Havel Airport Prague", "PRG"),
    "BUD": Airport("BUD", "Budapest", "Hungary", "Budapest Ferenc Liszt", "BUD"),
    "WAW": Airport("WAW", "Warsaw", "Poland", "Warsaw Chopin", "WAW"),
    "KRK": Airport("KRK", "Krakow", "Poland", "Krakow John Paul II", "KRK"),
    # Croatia
    "ZAG": Airport("ZAG", "Zagreb", "Croatia", "Zagreb Franjo Tudman", "ZAG"),
    "SPU": Airport("SPU", "Split", "Croatia", "Split Airport", "SPU"),
    "DBV": Airport("DBV", "Dubrovnik", "Croatia", "Dubrovnik Airport", "DBV"),
    # Turkey (Istanbul is a common hub)
    "IST": Airport("IST", "Istanbul", "Turkey", "Istanbul Airport", "IST"),
    # France - excluded as a destination by default; still valid connection points.
    "CDG": Airport("CDG", "Paris", "France", "Paris Charles de Gaulle", "PAR"),
    "ORY": Airport("ORY", "Paris", "France", "Paris Orly", "PAR"),
    "NCE": Airport("NCE", "Nice", "France", "Nice Cote d'Azur", "NCE"),
    "LYS": Airport("LYS", "Lyon", "France", "Lyon Saint-Exupery", "LYS"),
}

DEFAULT_DESTINATIONS = [
    "FCO", "MXP", "VCE", "FLR", "MAD", "BCN", "LIS", "OPO", "AMS", "ZRH", "GVA",
    "VIE", "FRA", "MUC", "LHR", "LGW", "DUB", "ATH", "CPH",
]
DEFAULT_EXCLUDED_DESTINATION_COUNTRIES = ["France"]

ALL_AIRPORTS: dict[str, Airport] = {**US_ORIGINS, **EUROPEAN_AIRPORTS}


def airport(code: str) -> Airport | None:
    return ALL_AIRPORTS.get(code.upper())


def country_of(code: str) -> str | None:
    a = airport(code)
    return a.country if a else None


def city_of(code: str) -> str:
    a = airport(code)
    return a.city if a else code


def metro_of(code: str) -> str:
    a = airport(code)
    return a.metro or code if a else code


def european_countries() -> list[str]:
    return sorted({a.country for a in EUROPEAN_AIRPORTS.values()})


def destinations_for_countries(countries: list[str]) -> list[str]:
    wanted = set(countries)
    return [c for c, a in EUROPEAN_AIRPORTS.items() if a.country in wanted]


def filter_excluded_countries(codes: list[str], excluded_countries: list[str]) -> list[str]:
    excluded = {c.lower() for c in excluded_countries}
    return [c for c in codes if (country_of(c) or "").lower() not in excluded]
