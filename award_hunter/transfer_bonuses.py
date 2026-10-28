"""Amex Membership Rewards conversion: airline miles -> Amex points actually required.

Formula (documented, tested in tests/test_transfers.py)::

    miles_per_amex_point = (miles_out / amex_points_in) * (1 + bonus_percent / 100)
    raw_points           = miles_needed / miles_per_amex_point
    amex_points          = ceil(raw_points / increment) * increment

* ``increment`` is the Amex transfer increment for the program (1,000 for most
  partners, 250 for JetBlue). Amex also enforces a 1,000-point minimum transfer.
* A bonus is applied only when it is marked active, today is within its
  effective/expiration window, and (by default) it is marked as verified. An
  unverified bonus is reported as an alternative calculation, never silently
  applied.
* Programs that are not Amex partners get ``amex_points_per_passenger = None``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .models import AmexCalculation
from .transfer_partners import PROGRAMS, ProgramInfo


@dataclass
class PartnerTerms:
    source: str
    program_name: str
    is_amex_partner: bool
    amex_points_in: int | None
    miles_out: int | None
    transfer_increment: int | None
    enabled: bool = True

    @property
    def ratio(self) -> float | None:
        if not self.is_amex_partner or not self.amex_points_in or not self.miles_out:
            return None
        return self.miles_out / self.amex_points_in

    @classmethod
    def from_program(cls, p: ProgramInfo) -> "PartnerTerms":
        return cls(p.source, p.program_name, p.is_amex_partner, p.amex_points_in, p.miles_out, p.transfer_increment)

    @classmethod
    def from_row(cls, row: dict) -> "PartnerTerms":
        return cls(row["source"], row["program_name"], bool(row["is_amex_partner"]), row.get("amex_points_in"),
                   row.get("miles_out"), row.get("transfer_increment"), bool(row.get("enabled", 1)))


@dataclass
class Bonus:
    source: str
    bonus_percent: float
    effective_date: date | None
    expiration_date: date | None
    verified: bool
    account_eligible: bool
    active: bool
    label: str | None = None

    def applies_on(self, day: date) -> bool:
        if not self.active or self.bonus_percent <= 0:
            return False
        if self.effective_date and day < self.effective_date:
            return False
        if self.expiration_date and day > self.expiration_date:
            return False
        return True

    @classmethod
    def from_row(cls, row: dict) -> "Bonus":
        def d(v: str | None) -> date | None:
            return date.fromisoformat(v) if v else None

        return cls(row["source"], float(row["bonus_percent"]), d(row.get("effective_date")), d(row.get("expiration_date")),
                   bool(row.get("verified", 0)), bool(row.get("account_eligible", 0)), bool(row.get("active", 1)),
                   label=row.get("notes") or f"{row['bonus_percent']:g}% bonus")


def round_up_to_increment(points: float, increment: int) -> int:
    increment = max(int(increment or 1000), 1)
    rounded = int(math.ceil(points / increment)) * increment
    return max(rounded, 1000)  # Amex minimum transfer is 1,000 points


def points_required(miles_needed: int, terms: PartnerTerms, bonus_percent: float = 0.0) -> int | None:
    ratio = terms.ratio
    if ratio is None or miles_needed <= 0:
        return None
    effective = ratio * (1 + bonus_percent / 100.0)
    return round_up_to_increment(miles_needed / effective, terms.transfer_increment or 1000)


class TransferEngine:
    """Combines partner terms and bonus table into :class:`AmexCalculation` objects."""

    def __init__(self, partners: dict[str, PartnerTerms] | None = None, bonuses: list[Bonus] | None = None,
                 *, apply_unverified_bonuses: bool = False, today: date | None = None) -> None:
        self.partners = partners or {s: PartnerTerms.from_program(p) for s, p in PROGRAMS.items()}
        self.bonuses = bonuses or []
        self.apply_unverified = apply_unverified_bonuses
        self.today = today or date.today()

    @classmethod
    def from_database(cls, db, *, apply_unverified_bonuses: bool = False, today: date | None = None) -> "TransferEngine":
        partners = {r["source"]: PartnerTerms.from_row(r) for r in db.transfer_partners()}
        bonuses = [Bonus.from_row(r) for r in db.transfer_bonuses(active_only=True)]
        return cls(partners, bonuses, apply_unverified_bonuses=apply_unverified_bonuses, today=today)

    def active_bonus(self, source: str) -> Bonus | None:
        candidates = [b for b in self.bonuses if b.source == source and b.applies_on(self.today)]
        if not candidates:
            return None
        return max(candidates, key=lambda b: b.bonus_percent)

    def calculate(self, source: str, miles_needed: int | None, passengers: int = 2) -> AmexCalculation:
        source = (source or "").lower()
        terms = self.partners.get(source)
        notes: list[str] = []
        if terms is None or not terms.is_amex_partner or terms.ratio is None:
            name = terms.program_name if terms else source
            return AmexCalculation(source=source, program_name=name, is_amex_partner=False, miles_needed=miles_needed,
                                   notes=["Not an American Express Membership Rewards transfer partner; shown for comparison only."])
        if not terms.enabled:
            notes.append("Program disabled in settings.")
        if miles_needed is None:
            return AmexCalculation(source=source, program_name=terms.program_name, is_amex_partner=True,
                                   standard_ratio=terms.ratio, transfer_increment=terms.transfer_increment or 1000,
                                   notes=notes + ["Mileage cost not provided by the API."])

        base = points_required(miles_needed, terms, 0.0)
        bonus = self.active_bonus(source)
        bonus_pct = 0.0
        label = None
        verified = True
        with_bonus = base
        if bonus:
            if bonus.verified or self.apply_unverified:
                bonus_pct = bonus.bonus_percent
                label = bonus.label
                verified = bonus.verified
                with_bonus = points_required(miles_needed, terms, bonus_pct)
                if not bonus.verified:
                    notes.append(f"Applied UNVERIFIED {bonus.bonus_percent:g}% bonus; without it: {base:,} points.")
                if not bonus.account_eligible:
                    notes.append("Bonus may be targeted; confirm your account is eligible before transferring.")
            else:
                alt = points_required(miles_needed, terms, bonus.bonus_percent)
                notes.append(f"Unverified {bonus.bonus_percent:g}% bonus on record would reduce cost to {alt:,} points; not applied.")
        if terms.ratio != 1.0:
            notes.append(f"Transfer ratio {terms.amex_points_in:,} Amex -> {terms.miles_out:,} miles.")
        assert base is not None and with_bonus is not None
        return AmexCalculation(
            source=source, program_name=terms.program_name, is_amex_partner=True, standard_ratio=terms.ratio,
            transfer_increment=terms.transfer_increment or 1000, bonus_percent=bonus_pct, bonus_label=label, bonus_verified=verified,
            miles_needed=miles_needed, amex_points_per_passenger=with_bonus, amex_points_per_passenger_no_bonus=base,
            amex_points_two_passengers=with_bonus * passengers if passengers else with_bonus, notes=notes,
        )
