"""Shared fixtures: fake settings, realistic mocked Seats.aero payloads, mock transport."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx
import pytest

from award_hunter.api_client import DailyBudget, InMemoryCache, SeatsAeroClient
from award_hunter.config import Settings
from award_hunter.database import Database

FAKE_KEY = "pro_TESTKEYTESTKEYTESTKEY1234"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(api_key=FAKE_KEY, database_path=tmp_path / "t.sqlite", api_daily_budget=50, max_retries=2)


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "t.sqlite")
    yield d
    d.close()


def make_trip(avail_id: str, origin: str, dest: str, dep: str, arr: str, *, miles: int = 55000, seats: int = 2,
              taxes: int | None = 12000, currency: str = "USD", segments: list[dict] | None = None, cabin: str = "business",
              carriers: str = "AC", flight_numbers: str = "AC 790", stops: int = 0, duration: int = 630) -> dict:
    segs = segments if segments is not None else [
        {"ID": f"{avail_id}-s1", "FlightNumber": flight_numbers, "OriginAirport": origin, "DestinationAirport": dest,
         "DepartsAt": dep, "ArrivesAt": arr, "AircraftName": "Boeing 787-9", "AircraftCode": "789", "Cabin": cabin,
         "FareClass": "I", "Distance": 5400, "Order": 0}
    ]
    return {
        "ID": f"{avail_id}-t1", "RouteID": "r1", "AvailabilityID": avail_id, "AvailabilitySegments": segs,
        "TotalDuration": duration, "Stops": stops, "Carriers": carriers, "RemainingSeats": seats, "MileageCost": miles,
        "TotalTaxes": taxes, "TaxesCurrency": currency, "TaxesCurrencySymbol": "$", "FlightNumbers": flight_numbers,
        "DepartsAt": dep, "ArrivesAt": arr, "Cabin": cabin, "Source": "aeroplan",
        "UpdatedAt": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
    }


def make_availability(avail_id: str, origin: str, dest: str, day: str, *, source: str = "aeroplan", miles: int = 55000,
                      seats: int | None = 2, taxes: int | None = 12000, currency: str = "USD", direct: bool = True,
                      airlines: str = "AC", trips: list[dict] | None = None, j_available: bool = True,
                      updated_hours_ago: float = 3, origin_region: str = "North America", dest_region: str = "Europe") -> dict:
    row = {
        "ID": avail_id, "RouteID": f"route-{origin}-{dest}",
        "Route": {"ID": f"route-{origin}-{dest}", "OriginAirport": origin, "OriginRegion": origin_region,
                  "DestinationAirport": dest, "DestinationRegion": dest_region, "NumDaysOut": 60, "Distance": 5400, "Source": source},
        "Date": day, "ParsedDate": f"{day}T00:00:00Z", "Source": source,
        "CreatedAt": (NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
        "UpdatedAt": (NOW - timedelta(hours=updated_hours_ago)).isoformat().replace("+00:00", "Z"),
        "TaxesCurrency": currency, "TaxesCurrencySymbol": "$",
        "YAvailable": True, "WAvailable": False, "JAvailable": j_available, "FAvailable": False,
        "YDirect": True, "WDirect": False, "JDirect": direct, "FDirect": False,
        "YMileageCost": "35000", "WMileageCost": "0", "JMileageCost": str(miles) if j_available else "0", "FMileageCost": "0",
        "YMileageCostRaw": 35000, "WMileageCostRaw": 0, "JMileageCostRaw": miles if j_available else 0, "FMileageCostRaw": 0,
        "YRemainingSeats": 4, "WRemainingSeats": 0, "JRemainingSeats": seats if seats is not None else 0, "FRemainingSeats": 0,
        "YAirlines": "AC", "WAirlines": "", "JAirlines": airlines if j_available else "", "FAirlines": "",
        "YTotalTaxes": 8000, "WTotalTaxes": 0, "FTotalTaxes": 0,
    }
    if taxes is not None:
        row["JTotalTaxes"] = taxes
    if trips is not None:
        row["AvailabilityTrips"] = trips
    return row


def build_sample_rows() -> list[dict]:
    rows = [
        make_availability("a1", "LAX", "FCO", "2027-04-08", miles=50000, seats=2, taxes=9500,
                          trips=[make_trip("a1", "LAX", "FCO", "2027-04-08T15:30:00", "2027-04-09T12:10:00", miles=50000, seats=2, taxes=9500,
                                           carriers="AC", flight_numbers="AC 790, AC 894", stops=1, duration=900,
                                           segments=[
                                               {"FlightNumber": "AC 790", "OriginAirport": "LAX", "DestinationAirport": "YYZ",
                                                "DepartsAt": "2027-04-08T15:30:00", "ArrivesAt": "2027-04-08T23:15:00", "Cabin": "business",
                                                "AircraftName": "Boeing 787-9", "Order": 0},
                                               {"FlightNumber": "AC 894", "OriginAirport": "YYZ", "DestinationAirport": "FCO",
                                                "DepartsAt": "2027-04-09T00:30:00", "ArrivesAt": "2027-04-09T12:10:00", "Cabin": "business",
                                                "AircraftName": "Boeing 787-9", "Order": 1}])]),
        # Seat count not reported, taxes missing
        make_availability("a2", "SFO", "MAD", "2027-04-10", source="flyingblue", miles=60000, seats=0, taxes=None, airlines="AF",
                          trips=[make_trip("a2", "SFO", "MAD", "2027-04-10T16:00:00", "2027-04-11T14:00:00", miles=60000, seats=0, taxes=None,
                                           carriers="AF", stops=1, duration=870)]),
        # Mixed cabin: long-haul business + economy intra-Europe
        make_availability("a3", "LAX", "VCE", "2027-04-09", source="virginatlantic", miles=47500, seats=2, taxes=45000, currency="USD", direct=False,
                          airlines="VS", trips=[make_trip("a3", "LAX", "VCE", "2027-04-09T18:00:00", "2027-04-10T16:00:00", miles=47500, seats=2,
                                                          taxes=45000, carriers="VS", stops=1, duration=960, segments=[
                                                              {"FlightNumber": "VS 8", "OriginAirport": "LAX", "DestinationAirport": "LHR",
                                                               "DepartsAt": "2027-04-09T18:00:00", "ArrivesAt": "2027-04-10T12:30:00", "Cabin": "business"},
                                                              {"FlightNumber": "VS 3001", "OriginAirport": "LHR", "DestinationAirport": "VCE",
                                                               "DepartsAt": "2027-04-10T13:50:00", "ArrivesAt": "2027-04-10T16:00:00", "Cabin": "economy"}])]),
        # Non-Amex partner
        make_availability("a4", "LAX", "MUC", "2027-04-08", source="united", miles=77000, seats=3, taxes=6000, airlines="LH"),
        # Economy only
        make_availability("a5", "SAN", "LHR", "2027-04-12", j_available=False),
        # Paris destination (excluded by default)
        make_availability("a6", "LAX", "CDG", "2027-04-08", source="flyingblue", miles=55000, seats=2, taxes=20000, airlines="AF"),
        # Return leg, open-jaw from Milan, 12 days later
        make_availability("a7", "MXP", "SFO", "2027-04-20", source="aeroplan", miles=55000, seats=2, taxes=15000, airlines="LH,AC",
                          origin_region="Europe", dest_region="North America",
                          trips=[make_trip("a7", "MXP", "SFO", "2027-04-20T10:00:00", "2027-04-20T19:30:00", miles=55000, seats=2, taxes=15000,
                                           carriers="LH,AC", flight_numbers="LH 247, AC 759", stops=1, duration=1050, segments=[
                                               {"FlightNumber": "LH 247", "OriginAirport": "MXP", "DestinationAirport": "FRA",
                                                "DepartsAt": "2027-04-20T10:00:00", "ArrivesAt": "2027-04-20T11:20:00", "Cabin": "business"},
                                               {"FlightNumber": "AC 759", "OriginAirport": "FRA", "DestinationAirport": "SFO",
                                                "DepartsAt": "2027-04-20T13:00:00", "ArrivesAt": "2027-04-20T19:30:00", "Cabin": "business"}])]),
        # Return leg with only 1 seat
        make_availability("a8", "FCO", "LAX", "2027-04-19", source="aeroplan", miles=52000, seats=1, taxes=14000, airlines="AC",
                          origin_region="Europe", dest_region="North America"),
    ]
    return rows


@pytest.fixture
def sample_rows() -> list[dict]:
    return build_sample_rows()


def search_response(rows: list[dict], has_more: bool = False, cursor: int | None = None) -> dict:
    return {"data": rows, "count": len(rows), "hasMore": has_more, "cursor": cursor,
            "moreURL": "/partnerapi/search?cursor=%d" % cursor if cursor else ""}


class MockApi:
    """Programmable mock of the Partner API for httpx.MockTransport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] | None = None
        self.quota = 900

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler:
            return self.handler(request)
        return httpx.Response(200, json=search_response([]), headers={"X-RateLimit-Remaining": str(self.quota)})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


@pytest.fixture
def mock_api() -> MockApi:
    return MockApi()


@pytest.fixture
def client(settings, mock_api) -> SeatsAeroClient:
    c = SeatsAeroClient(settings, cache=InMemoryCache(), budget=DailyBudget(50), transport=mock_api.transport(), sleep=lambda s: None)
    yield c
    c.close()
