from datetime import datetime, timezone

from award_hunter.models import Cabin, RawAvailability, SeatConfidence
from award_hunter.normalization import normalize_availability, normalize_many, parse_cabin, parse_utc
from tests.conftest import NOW, make_availability, make_trip


def _norm(row, **kw):
    return normalize_availability(RawAvailability.model_validate(row), retrieved_at=NOW, **kw)


def test_business_record_normalized(sample_rows):
    obs = _norm(sample_rows[0])
    assert obs.origin == "LAX" and obs.destination == "FCO" and obs.travel_date.isoformat() == "2027-04-08"
    assert obs.miles_per_passenger == 50000
    assert obs.remaining_seats == 2 and obs.seat_confidence == SeatConfidence.REPORTED
    assert obs.fees_known and obs.taxes_major == 95.0 and obs.taxes_currency == "USD"
    assert obs.program_name == "Air Canada Aeroplan"
    assert abs(obs.data_age_hours - 3) < 0.01
    best = obs.best_option()
    assert best.stops == 1 and best.connection_airports == ["YYZ"] and best.mixed_cabin is False
    assert best.overnight_layover is not None


def test_seat_count_not_reported_is_not_two_seats(sample_rows):
    obs = _norm(sample_rows[1])
    assert obs.remaining_seats is None
    assert obs.seat_confidence == SeatConfidence.NOT_REPORTED
    assert "seat_count" in obs.missing_fields


def test_missing_taxes_marked_not_known(sample_rows):
    obs = _norm(sample_rows[1])
    assert obs.fees_known is False and obs.taxes_minor_units is None and obs.taxes_major is None
    assert "taxes" in obs.missing_fields


def test_mixed_cabin_detected(sample_rows):
    obs = _norm(sample_rows[2])
    assert obs.best_option().mixed_cabin is True


def test_economy_only_record_dropped(sample_rows):
    assert _norm(sample_rows[4]) is None


def test_missing_cabin_data_on_segments_is_unknown_not_mixed():
    trip = make_trip("z", "LAX", "FCO", "2027-04-08T10:00:00", "2027-04-09T08:00:00",
                     segments=[{"FlightNumber": "X1", "OriginAirport": "LAX", "DestinationAirport": "FCO",
                                "DepartsAt": "2027-04-08T10:00:00", "ArrivesAt": "2027-04-09T08:00:00"}])
    obs = _norm(make_availability("z", "LAX", "FCO", "2027-04-08", trips=[trip]))
    assert obs.best_option().mixed_cabin is None
    assert obs.best_option().segments[0].cabin == Cabin.UNKNOWN


def test_no_trips_marks_flight_details_missing():
    obs = _norm(make_availability("n", "LAX", "FCO", "2027-04-08"))
    assert obs.flight_options == [] and obs.best_option() is None and "flight_details" in obs.missing_fields


def test_trip_level_data_fills_missing_route_level_fields():
    trip = make_trip("f", "LAX", "FCO", "2027-04-08T10:00:00", "2027-04-09T08:00:00", miles=48000, seats=3, taxes=5000)
    obs = _norm(make_availability("f", "LAX", "FCO", "2027-04-08", miles=0, seats=0, taxes=None, trips=[trip]))
    assert obs.miles_per_passenger == 48000 and obs.remaining_seats == 3 and obs.fees_known and obs.taxes_minor_units == 5000


def test_duplicates_removed():
    rows = [RawAvailability.model_validate(make_availability("d", "LAX", "FCO", "2027-04-08"))] * 3
    assert len(normalize_many(rows, retrieved_at=NOW)) == 1


def test_bad_date_skipped():
    row = make_availability("bad", "LAX", "FCO", "not-a-date")
    row["ParsedDate"] = None
    assert _norm(row) is None


def test_string_numbers_are_coerced():
    row = make_availability("s", "LAX", "FCO", "2027-04-08")
    row["JRemainingSeats"] = "2"
    row["JTotalTaxes"] = "12345"
    row["JMileageCostRaw"] = "0"
    obs = _norm(row)
    assert obs.remaining_seats == 2 and obs.taxes_minor_units == 12345 and obs.miles_per_passenger == 55000  # from JMileageCost


def test_availability_trips_non_list_ignored():
    row = make_availability("t", "LAX", "FCO", "2027-04-08")
    row["AvailabilityTrips"] = "null"
    assert _norm(row).flight_options == []


def test_parse_helpers():
    assert parse_cabin("Business") == Cabin.BUSINESS and parse_cabin(None) == Cabin.UNKNOWN and parse_cabin("J") == Cabin.BUSINESS
    assert parse_utc("2026-10-28T09:00:00Z") == datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
    assert parse_utc("garbage") is None


def test_zero_taxes_are_not_provided():
    obs = _norm(make_availability("z", "LAX", "FCO", "2027-04-08", taxes=0))
    assert obs.fees_known is False and obs.taxes_minor_units is None and "taxes" in obs.missing_fields
