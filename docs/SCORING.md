# Deal scoring formulas

Implemented in `award_hunter/scoring.py`; choose a priority preset (and optionally fine-tune
weights) in Settings → Scoring.

## Priority presets
| Preset | Weights (points / fees / convenience / confidence / quality / flexibility) | Points curve (per person, one way) | Fee curve (per person, one way) |
|---|---|---|---|
| **Minimize cash** (default) | 22 / 38 / 15 / 10 / 10 / 5 | ≤50k → 1.0, 60k → 0.85, 70k → 0.65, 85k → 0.4, 100k → 0.15, ≥120k → 0 | ≤$50 → 1.0, $125 → 0.85, $250 → 0.55, $400 → 0.25, $600 → 0.05, ≥$800 → 0 |
| Balanced (original spec) | 40 / 20 / 15 / 10 / 10 / 5 | table below | table below |
| Minimize points | 50 / 10 / 15 / 10 / 10 / 5 | ≤35k → 1.0, 45k → 0.8, 55k → 0.55, 65k → 0.3, 80k → 0.1, ≥95k → 0 | ≤$150 → 1.0, $300 → 0.8, $500 → 0.5, $800 → 0.2, ≥$1200 → 0 |

Under *Minimize cash*, round trips with equal scores are ordered by cash first, then points.
The dashboard also offers an **all-in cost** = cash + points × (cents per point, default 1.0¢)
as a ranking; it is a comparison aid, never a valuation.

The default search profile allows up to 90,000 Amex points per person one way (was 70,000)
so that low-fee programs such as Aeroplan at 75,000 are considered.

## Balanced preset factors

| Factor | Default weight | How the 0–1 factor is computed |
|--------|---------------:|--------------------------------|
| Amex points efficiency | 40 | Per person one way after transfer ratio/bonus. ≤40k → 1.0, 50k → 0.8, 60k → 0.6, 70k → 0.4, 85k → 0.15, ≥100k → 0 (linear between anchors). Non-partner → 0 and the award is capped at 49. |
| Taxes & fees | 20 | Per person one way, converted to USD with an editable approximate table. ≤$100 → 1.0, $200 → 0.85, $350 → 0.55, $600 → 0.2, ≥$900 → 0. **Unknown fees → 0.45** so missing data is never rewarded. |
| Flight convenience | 15 | Start 1.0; −0.25 per connection; −0.2 overnight layover; −0.15 layover > 4 h; −0.1 when stops are unknown; −0.5 mixed cabin. |
| Availability / data confidence | 10 | Reported seats ≥ passengers → 1.0; reported but fewer → 0.2; not reported → 0.5. Minus 0.2 if observation > 24 h old, 0.4 if > 72 h, 0.3 if unknown. |
| Airline & cabin quality | 10 | 0.7 baseline (business label, lie-flat **not** verified); +0.3 nonstop on a known long-haul business product; −0.3 mixed cabin; −0.1 unknown segment cabins. |
| Booking flexibility | 5 | 1.0 verified partner, no bonus assumed; 0.7 verified bonus assumed; 0.4 unverified bonus applied. |

Total = Σ(factor × weight) × 100 / Σ(weights). Tiers: 90+ Exceptional, 80–89 Excellent, 70–79 Worth considering, <70 hidden by default.

Round trips: points factor uses total Amex points for two ÷ (2 legs × passengers);
fees factor uses total known fees the same way (incomplete fees → 0.45);
convenience is the mean of both legs minus 0.1 for an open-jaw in Europe and 0.1
for different U.S. airports; confidence/quality/flexibility are leg means.

Data confidence is about how trustworthy the *observation* is. It is never a
statement that seats are bookable: every award carries
`requires_airline_verification` until you mark it verified in Deal Details.
