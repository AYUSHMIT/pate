"""Behavioral checks for the independent reference and its bounded domain."""

import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reference  # noqa: E402


class ReferenceContractTests(unittest.TestCase):
    def test_read_to_clear_returns_old_value_before_clearing(self):
        execution = reference.execute("read_to_clear", "original", 0x12345678)
        self.assertEqual(execution["reads"], [0x12345678, 0])
        self.assertEqual(execution["output"], 0x12345678)
        self.assertEqual(execution["final_state"], 0)
        self.assertEqual(
            execution["events"],
            [
                {"op": "read", "register": "status", "value": 0x12345678, "width_bits": 32},
                {"op": "read", "register": "status", "value": 0, "width_bits": 32},
                {"op": "write", "register": "output", "value": 0x12345678, "width_bits": 32},
            ],
        )

    def test_ram_preserves_both_read_values_and_state(self):
        execution = reference.execute("ram", "second_read", 0x80000001)
        self.assertEqual(execution["reads"], [0x80000001, 0x80000001])
        self.assertEqual(execution["output"], 0x80000001)
        self.assertEqual(execution["final_state"], 0x80000001)

    def test_second_read_changes_result_under_nonzero_read_to_clear_state(self):
        result = reference.compare("read_to_clear", "second_read", 1)
        self.assertFalse(result["equivalent"])
        self.assertEqual(result["original"]["output"], 1)
        self.assertEqual(result["patched"]["output"], 0)
        self.assertEqual(result["original"]["events"][:2], result["patched"]["events"][:2])

    def test_zero_is_a_real_equivalence_boundary(self):
        result = reference.compare("read_to_clear", "second_read", 0)
        self.assertTrue(result["equivalent"])
        self.assertEqual(result["original"]["reads"], [0, 0])

    def test_control_preserves_full_unsigned_values(self):
        for model in reference.MODELS:
            for initial in (0, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFC, 0xFFFFFFFF):
                with self.subTest(model=model, initial=initial):
                    self.assertEqual(reference.execute(model, "equivalent", initial)["output"], initial)

    def test_negative_control_changes_zero_one_and_all_ones(self):
        cases = ((0, 1), (1, 0), (0xFFFFFFFF, 0xFFFFFFFE))
        for model in reference.MODELS:
            for initial, expected in cases:
                with self.subTest(model=model, initial=initial):
                    result = reference.compare(model, "xor1", initial)
                    self.assertEqual(result["patched"]["output"], expected)
                    self.assertFalse(result["equivalent"])

    def test_rejects_noninteger_seeds_including_booleans(self):
        for initial in (True, False, None, "1", 1.0, float("nan"), float("inf"), [], {}):
            with self.subTest(initial=initial):
                with self.assertRaises(TypeError):
                    reference.execute("ram", "original", initial)

    def test_rejects_seeds_outside_uint32(self):
        for initial in (-1, 1 << 32, 1 << 80):
            with self.subTest(initial=initial):
                with self.assertRaises(ValueError):
                    reference.execute("ram", "original", initial)

    def test_rejects_unsupported_model_and_variant(self):
        with self.assertRaises(ValueError):
            reference.execute("fifo", "original", 1)
        with self.assertRaises(ValueError):
            reference.execute("ram", "unknown", 1)
        with self.assertRaises(TypeError):
            reference.execute(None, "original", 1)
        with self.assertRaises(TypeError):
            reference.execute("ram", False, 1)

    def test_bounded_domain_is_exact_and_not_duplicated(self):
        seeds = reference.seed_corpus()
        self.assertEqual(seeds[:256], tuple(range(256)))
        self.assertEqual(seeds[256:], (0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF))
        self.assertEqual(len(seeds), 260)
        self.assertEqual(len(set(seeds)), 260)

    def test_complete_bounded_comparison_matrix(self):
        summary = reference.summarize_corpus()
        self.assertEqual(summary["seed_count"], 260)
        self.assertEqual(summary["execution_count"], 2080)
        self.assertEqual(summary["comparison_count"], 1560)
        observed = {
            (row["model"], row["variant"]): (row["equal"], row["unequal"])
            for row in summary["comparisons"]
        }
        self.assertEqual(
            observed,
            {
                ("ram", "equivalent"): (260, 0),
                ("ram", "second_read"): (260, 0),
                ("ram", "xor1"): (0, 260),
                ("read_to_clear", "equivalent"): (260, 0),
                ("read_to_clear", "second_read"): (1, 259),
                ("read_to_clear", "xor1"): (0, 260),
            },
        )
        # The output is an actual interchange format, not Python-only data.
        self.assertEqual(json.loads(json.dumps(summary)), summary)


if __name__ == "__main__":
    unittest.main()
