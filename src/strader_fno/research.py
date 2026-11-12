from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .formulas import CANDIDATES
from .models import Candle, price


def compare_references(references: list[dict]) -> dict:
    if not references:
        raise ValueError("At least one reference is required")
    cases = []
    matches = {name: 0 for name in CANDIDATES}
    errors = {name: Decimal(0) for name in CANDIDATES}
    for reference in references:
        expected = [price(v) for v in reference["plot_values"]]
        if len(expected) != 7:
            raise ValueError("References must preserve all seven indicator plot values")
        raw = reference["previous_candle"]
        candle = Candle(date.fromisoformat(raw["date"]),
                        *(price(raw[key]) for key in ("open", "high", "low", "close")))
        session = date.fromisoformat(reference["session"])
        if candle.session >= session:
            raise ValueError("Reference candle must precede the screenshot session")
        centers = [(expected[0] + expected[1]) / 2,
                   (expected[2] + expected[3]) / 2,
                   (expected[4] + expected[5]) / 2]
        center = (candle.high + candle.low) / 2
        results = {}
        for name, formula in CANDIDATES.items():
            actual = formula(candle).as_plot_values()
            # Each displayed value has its own precision; SBI and Godrej use mixed precision.
            rounded = [v.quantize(Decimal(1).scaleb(e.as_tuple().exponent), rounding=ROUND_HALF_UP)
                       for v, e in zip(actual, expected)]
            delta = [abs(a - e) for a, e in zip(rounded, expected)]
            exact = rounded == expected
            matches[name] += exact
            errors[name] += sum(delta)
            results[name] = {"predicted": [str(v) for v in rounded],
                             "absolute_errors": [str(v) for v in delta],
                             "all_seven_match": exact}
        cases.append({"symbol": reference["symbol"], "session": reference["session"],
                      "previous_hl_midpoint": str(center),
                      "observed_midpoints": [str(v) for v in centers],
                      "observed_half_widths": [str((expected[0] - expected[1]) / 2),
                                                str((expected[2] - expected[3]) / 2),
                                                str((expected[5] - expected[4]) / 2)],
                      "candidate_results": results})
    return {"formula_status": "unconfirmed",
            "reference_count": len(references),
            "summary": {name: {"matching_cases": matches[name],
                                "total_absolute_error": str(errors[name])}
                        for name in CANDIDATES},
            "cases": cases,
            "note": "Matching examples supports a candidate; it does not uniquely identify the original formula."}
