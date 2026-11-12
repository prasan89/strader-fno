import json
import unittest
from decimal import Decimal
from pathlib import Path

from strader_fno.research import compare_references

REFERENCES = Path(__file__).resolve().parents[1] / "examples" / "references-2026-11-12.json"


class ResearchTests(unittest.TestCase):
    def test_all_five_symbols_have_the_previous_high_low_midpoint(self):
        result = compare_references(json.loads(REFERENCES.read_text()))
        centers = {case["symbol"]: Decimal(case["previous_hl_midpoint"]) for case in result["cases"]}
        self.assertEqual({"SBILIFE": Decimal("1774.10"), "UPL": Decimal("552.175"),
                          "GODREJIND": Decimal("978.45"), "DLF": Decimal("746.775"),
                          "NHPC": Decimal("79.105")}, centers)
        for case in result["cases"]:
            for observed in case["observed_midpoints"]:
                # Godrej's outer high is displayed at one decimal, unlike the other levels.
                self.assertLessEqual(abs(Decimal(observed) - centers[case["symbol"]]), Decimal("0.025"))

    def test_approximate_candidates_are_not_claimed_as_cracked(self):
        result = compare_references(json.loads(REFERENCES.read_text()))
        self.assertEqual("unconfirmed", result["formula_status"])
        self.assertEqual(5, result["reference_count"])
        self.assertEqual(1, result["summary"]["sbi_range_hypothesis"]["matching_cases"])
        self.assertTrue(result["cases"][0]["candidate_results"]["sbi_range_hypothesis"]["all_seven_match"])
        self.assertTrue(all(not case["candidate_results"]["sbi_range_hypothesis"]["all_seven_match"]
                            for case in result["cases"][1:]))
        self.assertEqual(7, len(result["cases"][0]["candidate_results"]["sbi_range_hypothesis"]["predicted"]))

    def test_current_session_ohlc_is_not_accepted_as_prior_data(self):
        reference = json.loads(REFERENCES.read_text())[:1]
        reference[0]["previous_candle"]["date"] = "2026-11-12"
        with self.assertRaises(ValueError):
            compare_references(reference)


if __name__ == "__main__":
    unittest.main()
