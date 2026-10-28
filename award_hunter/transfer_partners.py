"""American Express Membership Rewards (U.S.) transfer-partner reference data.

``PROGRAMS`` maps every Seats.aero ``source`` code we know about to a display name
and, where applicable, Amex transfer terms. Rows are seeded into SQLite on first
run and are fully editable from the Settings page, so you can correct a ratio the
day Amex changes it without touching code.

IMPORTANT: ratios below reflect publicly listed U.S. Membership Rewards terms as of
the ``last_verified`` date. Verify on americanexpress.com before transferring.
Amex transfers are generally irreversible.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

LAST_VERIFIED = "2026-10-28"


@dataclass
class ProgramInfo:
    source: str  # Seats.aero source code (lowercase)
    program_name: str
    airline: str
    alliance: str | None
    is_amex_partner: bool
    amex_points_in: int | None  # e.g. 1000 Amex points ...
    miles_out: int | None  # ... become this many miles
    transfer_increment: int | None  # Amex transfer increment (points)
    booking_url: str | None
    reports_seat_counts: str  # "yes" / "no" / "unknown" - validated at runtime, this is only a hint
    reports_taxes: str  # "yes" / "no" / "unknown"
    notes: str = ""
    last_verified: str = LAST_VERIFIED
    seats_aero_source_active: bool = True

    @property
    def ratio(self) -> float | None:
        if not self.is_amex_partner or not self.amex_points_in or not self.miles_out:
            return None
        return self.miles_out / self.amex_points_in

    def to_row(self) -> dict:
        return asdict(self)


PROGRAMS: dict[str, ProgramInfo] = {
    # ---- Amex MR transfer partners that are also Seats.aero sources ----------
    "aeroplan": ProgramInfo("aeroplan", "Air Canada Aeroplan", "Air Canada", "Star Alliance", True, 1000, 1000, 1000,
                            "https://www.aircanada.com/aeroplan", "yes", "yes",
                            "Strong for Star Alliance to Europe; low surcharges, small partner booking fee."),
    "flyingblue": ProgramInfo("flyingblue", "Air France-KLM Flying Blue", "Air France / KLM", "SkyTeam", True, 1000, 1000, 1000,
                              "https://www.flyingblue.com", "yes", "yes",
                              "Dynamic pricing; monthly Promo Rewards. Transfer bonuses from Amex are common but must be active."),
    "virginatlantic": ProgramInfo("virginatlantic", "Virgin Atlantic Flying Club", "Virgin Atlantic", None, True, 1000, 1000, 1000,
                                  "https://www.virginatlantic.com/flyingclub", "yes", "yes",
                                  "UK departures carry high surcharges and APD; check fees carefully."),
    "emirates": ProgramInfo("emirates", "Emirates Skywards", "Emirates", None, True, 1000, 1000, 1000,
                            "https://www.emirates.com/skywards", "yes", "yes",
                            "Routes via Dubai; long total travel time to Europe from the U.S. West Coast."),
    "etihad": ProgramInfo("etihad", "Etihad Guest", "Etihad", None, True, 1000, 1000, 1000,
                          "https://www.etihadguest.com", "unknown", "unknown", "Routes via Abu Dhabi."),
    "singapore": ProgramInfo("singapore", "Singapore KrisFlyer", "Singapore Airlines", "Star Alliance", True, 1000, 1000, 1000,
                             "https://www.singaporeair.com/krisflyer", "yes", "yes",
                             "Transfers can take 1-2 days to post. Star Alliance partner awards bookable online."),
    "qantas": ProgramInfo("qantas", "Qantas Frequent Flyer", "Qantas", "oneworld", True, 1000, 1000, 1000,
                          "https://www.qantas.com/frequentflyer", "unknown", "yes",
                          "Generally poor value for U.S.-Europe business; included for completeness."),
    "qatar": ProgramInfo("qatar", "Qatar Airways Privilege Club", "Qatar Airways", "oneworld", True, 1000, 1000, 1000,
                         "https://www.qatarairways.com/privilegeclub", "unknown", "yes",
                         "Avios-based. Routes via Doha; also books BA/Iberia partner space."),
    "delta": ProgramInfo("delta", "Delta SkyMiles", "Delta", "SkyTeam", True, 1000, 1000, 1000,
                         "https://www.delta.com/skymiles", "yes", "yes",
                         "Dynamic pricing; transatlantic business rarely under 70k but occasional flash sales."),
    "jetblue": ProgramInfo("jetblue", "JetBlue TrueBlue", "JetBlue", None, True, 250, 200, 250,
                           "https://trueblue.jetblue.com", "unknown", "yes",
                           "250 MR -> 200 TrueBlue. Mint to Europe is priced in cash-linked points; usually poor value."),
    "aeromexico": ProgramInfo("aeromexico", "Aeromexico Rewards", "Aeromexico", "SkyTeam", True, 1000, 1600, 1000,
                              "https://www.aeromexico.com/rewards", "unknown", "unknown",
                              "1000 MR -> 1600 points. Europe awards via Mexico City; verify partner pricing."),
    # ---- Amex MR partners NOT currently covered by Seats.aero -----------------
    "lifemiles": ProgramInfo("lifemiles", "Avianca LifeMiles", "Avianca", "Star Alliance", True, 1000, 1000, 1000,
                             "https://www.lifemiles.com", "unknown", "no",
                             "Amex partner but not a Seats.aero search source at the time of writing.",
                             seats_aero_source_active=False),
    "britishairways": ProgramInfo("britishairways", "British Airways Club", "British Airways", "oneworld", True, 1000, 1000, 1000,
                                  "https://www.britishairways.com/executive-club", "unknown", "yes",
                                  "Amex partner; not a Seats.aero source at the time of writing. High UK surcharges.",
                                  seats_aero_source_active=False),
    "iberia": ProgramInfo("iberia", "Iberia Club", "Iberia", "oneworld", True, 1000, 1000, 1000,
                          "https://www.iberia.com/iberiaplus", "unknown", "yes",
                          "Amex partner; not a Seats.aero source at the time of writing. Good MAD off-peak pricing.",
                          seats_aero_source_active=False),
    "asiamiles": ProgramInfo("asiamiles", "Cathay Pacific Asia Miles", "Cathay Pacific", "oneworld", True, 1000, 1000, 1000,
                             "https://www.cathaypacific.com/cx/en_US/asia-miles.html", "unknown", "yes",
                             "Amex partner; not a Seats.aero source at the time of writing.",
                             seats_aero_source_active=False),
    "ana": ProgramInfo("ana", "ANA Mileage Club", "ANA", "Star Alliance", True, 1000, 1000, 1000,
                       "https://www.ana.co.jp/en/us/amc/", "unknown", "yes",
                       "Amex partner; round-trip only; not a Seats.aero source at the time of writing.",
                       seats_aero_source_active=False),
    # ---- Seats.aero sources that are NOT Amex MR partners ---------------------
    "united": ProgramInfo("united", "United MileagePlus", "United", "Star Alliance", False, None, None, None,
                          "https://www.united.com/en/us/fly/mileageplus.html", "yes", "yes",
                          "Not an Amex partner (Chase UR / Bilt). Shown for comparison only."),
    "american": ProgramInfo("american", "American AAdvantage", "American", "oneworld", False, None, None, None,
                            "https://www.aa.com/aadvantage", "unknown", "yes", "Not an Amex partner."),
    "alaska": ProgramInfo("alaska", "Alaska Atmos Rewards", "Alaska", "oneworld", False, None, None, None,
                          "https://www.alaskaair.com", "yes", "yes", "Not an Amex partner (Bilt)."),
    "turkish": ProgramInfo("turkish", "Turkish Miles&Smiles", "Turkish Airlines", "Star Alliance", False, None, None, None,
                           "https://www.turkishairlines.com/miles-and-smiles", "unknown", "yes",
                           "Not an Amex partner (Citi / Capital One / Bilt)."),
    "lufthansa": ProgramInfo("lufthansa", "Lufthansa Miles & More", "Lufthansa", "Star Alliance", False, None, None, None,
                             "https://www.miles-and-more.com", "unknown", "yes", "Not an Amex partner."),
    "finnair": ProgramInfo("finnair", "Finnair Plus", "Finnair", "oneworld", False, None, None, None,
                           "https://www.finnair.com/plus", "unknown", "yes", "Not an Amex partner."),
    "eurobonus": ProgramInfo("eurobonus", "SAS EuroBonus", "SAS", "SkyTeam", False, None, None, None,
                             "https://www.flysas.com/eurobonus", "unknown", "yes", "Not an Amex U.S. partner."),
    "saudia": ProgramInfo("saudia", "Saudia Alfursan", "Saudia", "SkyTeam", False, None, None, None,
                          "https://www.saudia.com/alfursan", "unknown", "unknown", "Not an Amex partner."),
    "ethiopian": ProgramInfo("ethiopian", "Ethiopian ShebaMiles", "Ethiopian", "Star Alliance", False, None, None, None,
                             "https://www.ethiopianairlines.com/shebamiles", "unknown", "unknown", "Not an Amex partner."),
    "velocity": ProgramInfo("velocity", "Virgin Australia Velocity", "Virgin Australia", None, False, None, None, None,
                            "https://experience.velocityfrequentflyer.com", "unknown", "yes", "Not an Amex U.S. partner."),
    "connectmiles": ProgramInfo("connectmiles", "Copa ConnectMiles", "Copa", "Star Alliance", False, None, None, None,
                                "https://www.copaair.com/connectmiles", "unknown", "unknown", "Not an Amex partner."),
    "azul": ProgramInfo("azul", "Azul Fidelidade", "Azul", None, False, None, None, None,
                        "https://www.voeazul.com.br", "unknown", "unknown", "Not an Amex partner."),
    "smiles": ProgramInfo("smiles", "GOL Smiles", "GOL", None, False, None, None, None,
                          "https://www.smiles.com.br", "unknown", "unknown", "Not an Amex partner."),
    "frontier": ProgramInfo("frontier", "Frontier Miles", "Frontier", None, False, None, None, None,
                            "https://www.flyfrontier.com", "unknown", "unknown", "Not relevant for Europe."),
    "spirit": ProgramInfo("spirit", "Spirit Free Spirit", "Spirit", None, False, None, None, None,
                          "https://www.spirit.com", "unknown", "unknown", "Not relevant for Europe."),
}


def amex_partner_sources(active_only: bool = True) -> list[str]:
    return [s for s, p in PROGRAMS.items() if p.is_amex_partner and (p.seats_aero_source_active or not active_only)]


def program_name(source: str | None) -> str:
    if not source:
        return "Unknown program"
    info = PROGRAMS.get(source.lower())
    return info.program_name if info else source


def program(source: str | None) -> ProgramInfo | None:
    return PROGRAMS.get((source or "").lower())


DEFAULT_SEARCH_SOURCES = amex_partner_sources(active_only=True)


# --------------------------------------------------------------------------- #
# Sweet spots: the best price each program realistically charges for U.S. West
# Coast -> Europe business class, one way, in that program's miles. An award at
# or under the threshold is flagged "sweet spot" - it is as good as that program
# gets, so it is worth acting on quickly. Programs with no attractive floor for
# this route (routing via the Gulf, dynamic-only pricing) have no entry.
# Figures reflect published charts / typical promo levels as of LAST_VERIFIED;
# edit here when a program devalues.
# --------------------------------------------------------------------------- #
SWEET_SPOTS: dict[str, dict] = {
    "aeroplan": {"miles": 70_000, "best": 60_000,
                 "label": "Aeroplan partner chart floor (60k under 6,000 mi, 70k to 8,000 mi) with tiny fees"},
    "flyingblue": {"miles": 50_000, "best": 37_500,
                   "label": "Flying Blue Promo Rewards level (monthly 25% off; regular West Coast business starts ~60k)"},
    "virginatlantic": {"miles": 47_500, "best": 47_500,
                       "label": "Virgin Atlantic Upper Class off-peak LAX/SFO-LHR (watch the ~$700 surcharges)"},
    "delta": {"miles": 75_000, "best": 50_000,
              "label": "Delta One at or under 75k is a flash-sale level; 50k is the rare floor"},
    "qatar": {"miles": 62_500, "best": 62_500,
              "label": "Avios off-peak partner business U.S. West Coast-Europe (BA/Iberia; check surcharges)"},
}


def sweet_spot(source: str, miles: int | None) -> str | None:
    """Return 'best' / 'sweet' / None for a one-way business award priced in the program's miles."""
    if miles is None:
        return None
    spec = SWEET_SPOTS.get((source or "").lower())
    if not spec:
        return None
    if miles <= spec["best"]:
        return "best"
    if miles <= spec["miles"]:
        return "sweet"
    return None
