from datetime import date

from award_hunter.aircraft import lie_flat_likely
from award_hunter.booking import booking_link, link_is_deep, transfer_speed, transfer_warning
from award_hunter.geo import distance_km, ground_cost_pp_usd, positioning_cost_pp_usd
from award_hunter.models import RawAvailability
from award_hunter.normalization import normalize_availability
from tests.conftest import NOW, make_availability, make_trip


def test_distances_and_ground_costs():
    assert 240 < distance_km("MUC", "ZRH") < 280
    cost, mode = ground_cost_pp_usd("MUC", "ZRH")
    assert mode == "train" and 55 <= cost <= 65
    assert ground_cost_pp_usd("LHR", "LGW") == (0.0, "same city")
    cost, mode = ground_cost_pp_usd("FCO", "DUB")
    assert mode == "flight" and 90 <= cost <= 200
    assert ground_cost_pp_usd("XXX", "FCO") is None


def test_positioning_costs():
    assert positioning_cost_pp_usd("SAN", "SNA")[0] == 50.0  # same metro? no: SNA metro is LAX -> short drive
    assert positioning_cost_pp_usd("SAN", "SAN") == (0.0, "home")
    assert positioning_cost_pp_usd("SAN", "SFO") == (130.0, "short flight")
    assert positioning_cost_pp_usd("SAN", "SEA") == (170.0, "flight")


def test_booking_links():
    url = booking_link("aeroplan", "lax", "fra", date(2027, 1, 26), 2)
    assert url.startswith("https://www.aircanada.com/aeroplan/redeem/availability/outbound?org0=LAX&dest0=FRA&departureDate0=2027-01-26&ADT=2")
    url = booking_link("flyingblue", "SAN", "AMS", date(2027, 1, 19))
    assert "connections=SAN:A>20270119>AMS:A" in url and "cabinClass=BUSINESS" in url
    assert booking_link("virginatlantic", "LAX", "LHR", date(2027, 1, 1)) == "https://www.virginatlantic.com/flyingclub"
    assert link_is_deep("aeroplan") and not link_is_deep("delta")


def test_transfer_warnings():
    assert transfer_speed("aeroplan") == "instant" and transfer_speed("singapore") == "1–2 days"
    assert transfer_warning("aeroplan", 2) is None
    assert "1–2 days" in transfer_warning("singapore", 2)
    assert transfer_warning("singapore", 6) is None
    assert transfer_warning("etihad", None) is not None


def _obs(aircraft_name, aircraft_code, flight="LH 453"):
    seg = {"FlightNumber": flight, "OriginAirport": "LAX", "DestinationAirport": "MUC", "DepartsAt": "2027-04-08T15:00:00",
           "ArrivesAt": "2027-04-09T11:00:00", "Cabin": "business", "AircraftName": aircraft_name, "AircraftCode": aircraft_code, "Distance": 5900}
    row = make_availability("a", "LAX", "MUC", "2027-04-08", trips=[make_trip("a", "LAX", "MUC", "2027-04-08T15:00:00", "2027-04-09T11:00:00",
                                                                             flight_numbers=flight, segments=[seg])])
    return normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW)


def test_lie_flat_detection():
    assert lie_flat_likely(_obs("Airbus A350-900", "359"))[0] == "yes"
    assert lie_flat_likely(_obs("Boeing 757-200", "752"))[0] == "no"
    assert lie_flat_likely(_obs("Airbus A321neo", "32Q", flight="B6 1234"))[0] == "yes"  # JetBlue Mint
    state, label = lie_flat_likely(_obs(None, None))
    assert state == "?" and label == ""
