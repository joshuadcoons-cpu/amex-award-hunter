"""Pydantic models for raw Seats.aero payloads and the normalized internal records.

Raw models are deliberately permissive (``extra="allow"``) because the Partner API
adds fields over time; we only *require* the handful of fields the app depends on.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# Raw API models
# --------------------------------------------------------------------------- #


class RawRoute(BaseModel):
    model_config = ConfigDict(extra="allow")

    ID: str
    OriginAirport: str
    DestinationAirport: str
    OriginRegion: Optional[str] = None
    DestinationRegion: Optional[str] = None
    NumDaysOut: Optional[int] = None
    Distance: Optional[int] = None
    Source: Optional[str] = None


class RawSegment(BaseModel):
    model_config = ConfigDict(extra="allow")

    ID: Optional[str] = None
    FlightNumber: Optional[str] = None
    OriginAirport: Optional[str] = None
    DestinationAirport: Optional[str] = None
    DepartsAt: Optional[str] = None
    ArrivesAt: Optional[str] = None
    AircraftName: Optional[str] = None
    AircraftCode: Optional[str] = None
    FareClass: Optional[str] = None
    Cabin: Optional[str] = None
    Distance: Optional[int] = None
    Order: Optional[int] = None


class RawTrip(BaseModel):
    """Flight-level record (``AvailabilityTrips[]`` or ``/trips/{id}`` data[])."""

    model_config = ConfigDict(extra="allow")

    ID: Optional[str] = None
    RouteID: Optional[str] = None
    AvailabilityID: Optional[str] = None
    AvailabilitySegments: Optional[list[RawSegment]] = None
    TotalDuration: Optional[int] = None  # minutes
    Stops: Optional[int] = None
    Carriers: Optional[str] = None
    RemainingSeats: Optional[int] = None
    MileageCost: Optional[int] = None
    TotalTaxes: Optional[int] = None  # minor units (cents) in most programs
    TaxesCurrency: Optional[str] = None
    TaxesCurrencySymbol: Optional[str] = None
    FlightNumbers: Optional[str] = None
    DepartsAt: Optional[str] = None
    ArrivesAt: Optional[str] = None
    Cabin: Optional[str] = None
    Source: Optional[str] = None
    UpdatedAt: Optional[str] = None
    CreatedAt: Optional[str] = None
    Filtered: Optional[bool] = None

    @field_validator("TotalTaxes", "MileageCost", "RemainingSeats", "Stops", "TotalDuration", mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> Any:
        if v in (None, ""):
            return None
        if isinstance(v, str):
            try:
                return int(float(v))
            except ValueError:
                return None
        return v


class RawAvailability(BaseModel):
    """One row of ``/search`` or ``/availability`` ``data[]``."""

    model_config = ConfigDict(extra="allow")

    ID: str
    RouteID: Optional[str] = None
    Route: RawRoute
    Date: str
    ParsedDate: Optional[str] = None
    Source: Optional[str] = None
    UpdatedAt: Optional[str] = None
    CreatedAt: Optional[str] = None
    TaxesCurrency: Optional[str] = None
    TaxesCurrencySymbol: Optional[str] = None

    YAvailable: Optional[bool] = None
    WAvailable: Optional[bool] = None
    JAvailable: Optional[bool] = None
    FAvailable: Optional[bool] = None
    YDirect: Optional[bool] = None
    WDirect: Optional[bool] = None
    JDirect: Optional[bool] = None
    FDirect: Optional[bool] = None
    YMileageCost: Optional[str] = None
    WMileageCost: Optional[str] = None
    JMileageCost: Optional[str] = None
    FMileageCost: Optional[str] = None
    YMileageCostRaw: Optional[int] = None
    WMileageCostRaw: Optional[int] = None
    JMileageCostRaw: Optional[int] = None
    FMileageCostRaw: Optional[int] = None
    YDirectMileageCost: Optional[int] = None
    JDirectMileageCost: Optional[int] = None
    FDirectMileageCost: Optional[int] = None
    YTotalTaxes: Optional[int] = None
    WTotalTaxes: Optional[int] = None
    JTotalTaxes: Optional[int] = None
    FTotalTaxes: Optional[int] = None
    YRemainingSeats: Optional[int] = None
    WRemainingSeats: Optional[int] = None
    JRemainingSeats: Optional[int] = None
    FRemainingSeats: Optional[int] = None
    JDirectRemainingSeats: Optional[int] = None
    YAirlines: Optional[str] = None
    WAirlines: Optional[str] = None
    JAirlines: Optional[str] = None
    FAirlines: Optional[str] = None
    JDirectAirlines: Optional[str] = None
    AvailabilityTrips: Optional[list[RawTrip]] = None

    @field_validator("AvailabilityTrips", mode="before")
    @classmethod
    def _trips_list(cls, v: Any) -> Any:
        # Without include_trips the API may send null or a string; only lists are useful.
        return v if isinstance(v, list) else None

    @field_validator(
        "YMileageCostRaw", "WMileageCostRaw", "JMileageCostRaw", "FMileageCostRaw",
        "YTotalTaxes", "WTotalTaxes", "JTotalTaxes", "FTotalTaxes",
        "YRemainingSeats", "WRemainingSeats", "JRemainingSeats", "FRemainingSeats",
        "JDirectRemainingSeats", "JDirectMileageCost", "YDirectMileageCost", "FDirectMileageCost",
        mode="before",
    )
    @classmethod
    def _coerce_int(cls, v: Any) -> Any:
        if v in (None, ""):
            return None
        if isinstance(v, str):
            try:
                return int(float(v))
            except ValueError:
                return None
        return v

    @field_validator("YMileageCost", "WMileageCost", "JMileageCost", "FMileageCost", mode="before")
    @classmethod
    def _coerce_str(cls, v: Any) -> Any:
        if v is None:
            return None
        return str(v)


class RawSearchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[RawAvailability] = Field(default_factory=list)
    count: Optional[int] = None
    hasMore: bool = False
    cursor: Optional[int] = None
    moreURL: Optional[str] = None


class RawBookingLink(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: Optional[str] = None
    link: Optional[str] = None
    primary: Optional[bool] = None


class RawTripsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[RawTrip] = Field(default_factory=list)
    booking_links: Optional[list[RawBookingLink]] = None
    revalidation_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# Normalized internal models
# --------------------------------------------------------------------------- #


class Cabin(str, Enum):
    ECONOMY = "economy"
    PREMIUM = "premium"
    BUSINESS = "business"
    FIRST = "first"
    UNKNOWN = "unknown"


class DataSourceKind(str, Enum):
    CACHED = "cached"  # Seats.aero cached availability (all Pro-plan results)
    LIVE = "live"  # Not available on Pro plan; reserved for future use


class VerificationStatus(str, Enum):
    REQUIRES_AIRLINE_VERIFICATION = "requires_airline_verification"
    USER_VERIFIED = "user_verified"
    USER_REJECTED = "user_rejected"


class SeatConfidence(str, Enum):
    REPORTED = "reported"  # program reports a specific remaining seat count
    NOT_REPORTED = "not_reported"  # program doesn't publish seat counts (0 / null)
    UNKNOWN = "unknown"


class Segment(BaseModel):
    flight_number: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    departs_at_local: Optional[str] = None
    arrives_at_local: Optional[str] = None
    aircraft_name: Optional[str] = None
    aircraft_code: Optional[str] = None
    cabin: Cabin = Cabin.UNKNOWN
    fare_class: Optional[str] = None
    distance_miles: Optional[int] = None


class FlightOption(BaseModel):
    """A flight-level itinerary for one availability (from AvailabilityTrips / trips)."""

    trip_id: Optional[str] = None
    availability_id: str
    flight_numbers: Optional[str] = None
    carriers: Optional[str] = None
    stops: Optional[int] = None
    total_duration_minutes: Optional[int] = None
    departs_at_local: Optional[str] = None
    arrives_at_local: Optional[str] = None
    cabin: Cabin = Cabin.UNKNOWN
    mileage_cost: Optional[int] = None
    remaining_seats: Optional[int] = None
    taxes_minor_units: Optional[int] = None
    taxes_currency: Optional[str] = None
    segments: list[Segment] = Field(default_factory=list)
    connection_airports: list[str] = Field(default_factory=list)
    mixed_cabin: Optional[bool] = None  # None = unknown (no segment cabins supplied)
    overnight_layover: Optional[bool] = None
    longest_layover_minutes: Optional[int] = None
    updated_at_utc: Optional[datetime] = None
    filtered: Optional[bool] = None


class AwardObservation(BaseModel):
    """Normalized business-class award record for one route/date/program."""

    # Identity
    internal_id: str
    availability_id: str
    route_id: Optional[str] = None
    source: str  # Seats.aero program code, e.g. "aeroplan"
    program_name: str
    provider_endpoint: str = "/search"
    data_kind: DataSourceKind = DataSourceKind.CACHED

    # Flight
    origin: str
    destination: str
    origin_region: Optional[str] = None
    destination_region: Optional[str] = None
    travel_date: date
    cabin: Cabin = Cabin.BUSINESS
    direct_available: Optional[bool] = None
    airlines: Optional[str] = None  # comma-separated marketing/operating codes as reported
    distance_miles: Optional[int] = None

    # Award
    miles_per_passenger: Optional[int] = None
    miles_display: Optional[str] = None
    remaining_seats: Optional[int] = None
    seat_confidence: SeatConfidence = SeatConfidence.UNKNOWN
    taxes_minor_units: Optional[int] = None
    taxes_currency: Optional[str] = None
    fees_known: bool = False

    # Trips
    flight_options: list[FlightOption] = Field(default_factory=list)
    best_option_index: Optional[int] = None

    # Freshness / verification
    source_updated_at_utc: Optional[datetime] = None
    retrieved_at_utc: datetime
    verification: VerificationStatus = VerificationStatus.REQUIRES_AIRLINE_VERIFICATION
    missing_fields: list[str] = Field(default_factory=list)

    @property
    def taxes_major(self) -> Optional[float]:
        if self.taxes_minor_units is None:
            return None
        return self.taxes_minor_units / 100.0

    @property
    def data_age_hours(self) -> Optional[float]:
        if self.source_updated_at_utc is None:
            return None
        delta = self.retrieved_at_utc - self.source_updated_at_utc
        return max(delta.total_seconds(), 0) / 3600.0

    def best_option(self) -> Optional[FlightOption]:
        if self.best_option_index is None or not self.flight_options:
            return None
        if 0 <= self.best_option_index < len(self.flight_options):
            return self.flight_options[self.best_option_index]
        return None


class AmexCalculation(BaseModel):
    source: str
    program_name: str
    is_amex_partner: bool
    standard_ratio: Optional[float] = None  # miles received per 1 Amex point
    transfer_increment: int = 1000
    bonus_percent: float = 0.0
    bonus_label: Optional[str] = None
    bonus_verified: bool = True
    miles_needed: Optional[int] = None
    amex_points_per_passenger: Optional[int] = None
    amex_points_per_passenger_no_bonus: Optional[int] = None
    amex_points_two_passengers: Optional[int] = None
    notes: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    total: float
    components: dict[str, float]
    penalties: list[str] = Field(default_factory=list)
    tier: str


class ScoredAward(BaseModel):
    award: AwardObservation
    amex: AmexCalculation
    score: ScoreBreakdown
    positioning_required: bool = False
    hidden_reasons: list[str] = Field(default_factory=list)


class RoundTrip(BaseModel):
    outbound: ScoredAward
    inbound: ScoredAward
    trip_days: int
    open_jaw_europe: bool
    open_jaw_us: bool
    total_amex_points_two: Optional[int]
    total_fees_two_major: Optional[float]
    fees_complete: bool
    fees_currency: Optional[str]
    score: ScoreBreakdown
    warnings: list[str] = Field(default_factory=list)
    destination_country: Optional[str] = None
    destination_city: Optional[str] = None
    # Rough extras that make the all-in cash honest (per two passengers, USD). None = not applicable / unknown.
    ground_cost_two_usd: Optional[float] = None
    ground_cost_note: Optional[str] = None
    positioning_cost_two_usd: Optional[float] = None
    positioning_note: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        return f"{self.outbound.award.availability_id}|{self.inbound.award.availability_id}"
