"""Regressions for false success, mixed evidence, and lost volatile accesses."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build  # noqa: E402
import run as runner  # noqa: E402


class ResultClassificationTests(unittest.TestCase):
    def test_only_terminal_constructor_establishes_equivalence(self):
        self.assertEqual(
            runner.classify_result(0, "progress\n\x1b[32mEquivalent\x1b[0m\n", "")["status"],
            "equivalent",
        )
        for stdout in ("", "Binaries are observably equivalent\n", "Equivalent\nunfinished work\n"):
            with self.subTest(stdout=stdout):
                self.assertEqual(runner.classify_result(0, stdout, "")["status"], "unclassified")

    def test_zero_exit_observable_inequivalence_is_not_success(self):
        result = runner.classify_result(0, 'Inequivalent ["Observable differences at: node."]\n', "")
        self.assertEqual(result["status"], "reported_observable_difference")
        self.assertEqual(result["raw_status"], "Inequivalent")
        self.assertTrue(result["observable_difference_reported"])

    def test_conditional_result_remains_conditional(self):
        self.assertEqual(
            runner.classify_result(0, "ConditionallyEquivalent\n", "")["status"],
            "conditionally_equivalent",
        )

    def test_incomplete_analysis_is_not_a_validated_counterexample(self):
        reasons = (
            "Control flow desynchronization at:",
            "Failed to discharge assertion at:",
            "Ran out of gas while widening equivalence domains for:",
        )
        for reason in reasons:
            for observable in (False, True):
                with self.subTest(reason=reason, observable=observable):
                    messages = [reason + " node."]
                    if observable:
                        messages.append("Observable differences at: another node.")
                    result = runner.classify_result(0, "Inequivalent " + json.dumps(messages), "")
                    self.assertEqual(result["status"], "inconclusive")
                    self.assertIn(reason, result["analysis_limit_reasons"])
                    self.assertEqual(result["observable_difference_reported"], observable)
        self.assertEqual(runner.classify_result(0, "Inequivalent []", "")["status"], "inconclusive")

    def test_nonzero_exit_cannot_reuse_an_equivalent_marker(self):
        self.assertEqual(
            runner.classify_result(1, "Equivalent\n", "solver crashed")["status"],
            "analysis_error",
        )

    def test_unsupported_unknown_and_wall_timeout_are_distinct(self):
        self.assertEqual(
            runner.classify_result(1, "", "UnsupportedArchitecture EM_X86_64")["status"],
            "unsupported",
        )
        self.assertEqual(
            runner.classify_result(1, "", "ObservabilityError: UNKNOWN result")["status"],
            "inconclusive",
        )
        self.assertEqual(
            runner.classify_result(0, "Equivalent\n", "", timed_out=True)["status"],
            "timeout",
        )


class ProcessAndEvidenceTests(unittest.TestCase):
    def test_missing_executable_is_a_setup_failure_and_overwrites_no_prior_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original, patched, config = (directory / name for name in ("original", "patched", "config"))
            for path in (original, patched, config):
                path.write_text("fixture input\n")
            # A failed current invocation must not read an old log as its result.
            (directory / "probe.stdout.log").write_text("Equivalent\n")
            result = runner.pate_case(
                directory / "missing-executable", "Z3", original, patched, config,
                "audit_entry", "probe", True, directory,
            )
            self.assertEqual(result["status"], "setup_error")
            self.assertFalse(result["expectation_met"])
            self.assertIsNone(result["returncode"])
            self.assertEqual((directory / "probe.stdout.log").read_text(), "")
            self.assertTrue((directory / "probe.stderr.log").read_text())

    def test_process_timeout_retains_output_without_claiming_equivalence(self):
        result = runner.run_process(
            [sys.executable, "-c", "import time; print('Equivalent', flush=True); time.sleep(10)"],
            timeout=1.0,
        )
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["setup_error"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "Equivalent\n")
        self.assertEqual(
            runner.classify_result(result["returncode"], result["stdout"], result["stderr"], True)["status"],
            "timeout",
        )

    def test_nonempty_output_is_rejected_without_destroying_prior_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            runner.prepare_output(output)
            previous = output / "pate-results.json"
            previous.write_text('[{"status":"equivalent"}]\n')
            before = previous.read_bytes()
            with self.assertRaises(ValueError):
                runner.prepare_output(output)
            self.assertEqual(previous.read_bytes(), before)

    def test_manifest_detects_changes_to_inputs_binary_config_and_inspection_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            files = ("fixture.c", "fixture.elf", "fixture.so", "fixture.toml", "fixture.dis", "fixture.header")
            for name in files:
                (directory / name).write_text("original " + name)
            manifest = {
                "sources": {"fixture.c": runner.sha256(directory / "fixture.c")},
                "binaries": [{
                    "path": "fixture.elf", "sha256": runner.sha256(directory / "fixture.elf"),
                    "config": "fixture.toml", "config_sha256": runner.sha256(directory / "fixture.toml"),
                    "disassembly": "fixture.dis", "disassembly_sha256": runner.sha256(directory / "fixture.dis"),
                    "elf_header": "fixture.header", "elf_header_sha256": runner.sha256(directory / "fixture.header"),
                }],
                "native_libraries": [{"path": "fixture.so", "sha256": runner.sha256(directory / "fixture.so")}],
            }
            with patch.object(runner, "REPO", directory):
                runner.validate_manifest(directory, manifest)
                for name in files:
                    with self.subTest(name=name):
                        path = directory / name
                        before = path.read_bytes()
                        path.write_bytes(before + b" changed")
                        with self.assertRaises(ValueError):
                            runner.validate_manifest(directory, manifest)
                        path.write_bytes(before)

    def test_final_evidence_hashes_cover_nested_pate_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            build_dir, output = directory / "build", directory / "results"
            build_dir.mkdir()
            (build_dir / "manifest.json").write_text(json.dumps({
                "upstream_commit": build.UPSTREAM, "sources": {}, "binaries": [], "native_libraries": [],
            }))

            def fake_pate_checks(_build, _manifest, _pate, _solver, evidence, _timeout):
                logs = evidence / "pate-logs"
                logs.mkdir()
                (logs / "probe.stdout.log").write_text("Equivalent\n")
                (logs / "probe.stderr.log").write_text("")
                (logs / "baseline.toml").write_text("observable-memory = []\n")
                return {"cases": 1, "expectations_met": 1}

            argv = ["run.py", "--build-dir", str(build_dir), "--output", str(output), "--pate", sys.executable]
            with patch.object(sys, "argv", argv), \
                    patch.object(runner.shutil, "which", return_value=sys.executable), \
                    patch.object(runner, "version", return_value="test fixture version"), \
                    patch.object(runner, "native_checks", return_value={"failed": 0}), \
                    patch.object(runner, "powerpc_checks", return_value={"failed": 0}), \
                    patch.object(runner, "pate_checks", side_effect=fake_pate_checks), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(), 0)
            summary = json.loads((output / "summary.json").read_text())
            for relative in ("pate-logs/probe.stdout.log", "pate-logs/probe.stderr.log", "pate-logs/baseline.toml"):
                self.assertEqual(summary["evidence_sha256"][relative], runner.sha256(output / relative))


def listing(instructions):
    """A controlled objdump-shaped listing; instruction bytes are irrelevant here."""
    return "00010000 <audit_entry>:\n" + "\n".join(
        f" {0x10000 + 4 * index:x}:  00 00 00 00  {instruction}"
        for index, instruction in enumerate(instructions)
    )


class AccessInspectionTests(unittest.TestCase):
    instructions = [
        "lis r9,4099", "lwz r3,0(r9)", "lwz r4,0(r9)",
        "ori r9,r9,61440", "stw r3,0(r9)", "blr",
    ]

    def test_expected_straight_line_accesses_are_recognized(self):
        result = build.inspect_accesses(listing(self.instructions))
        self.assertEqual((result["state_loads"], result["output_stores"]), (2, 1))
        self.assertEqual([e["address"] for e in result["accesses"]],
                         [build.STATE_ADDRESS, build.STATE_ADDRESS, build.OUTPUT_ADDRESS])

    def test_missing_volatile_load_or_return_is_rejected(self):
        for instructions in (self.instructions[:2] + self.instructions[3:], self.instructions[:-1]):
            with self.subTest(instructions=instructions):
                with self.assertRaises(ValueError):
                    build.inspect_accesses(listing(instructions))

    def test_output_before_second_load_is_rejected(self):
        instructions = ["lis r9,4099", "lwz r3,0(r9)", "lis r10,4099", "ori r10,r10,61440",
                        "stw r3,0(r10)", "lwz r4,0(r9)", "blr"]
        with self.assertRaises(ValueError):
            build.inspect_accesses(listing(instructions))

    def test_extra_update_store_cannot_hide_a_second_output(self):
        instructions = self.instructions[:-1] + ["stwu r3,0(r9)", "blr"]
        with self.assertRaises(ValueError):
            build.inspect_accesses(listing(instructions))

    def test_branches_calls_and_wrong_width_memory_accesses_are_rejected(self):
        for operation in ("bne 10010", "bl 20000", "bctrl", "sth r3,0(r9)"):
            with self.subTest(operation=operation):
                instructions = self.instructions[:3] + [operation] + self.instructions[3:]
                with self.assertRaises(ValueError):
                    build.inspect_accesses(listing(instructions))

    def test_d_form_zero_base_does_not_use_register_zero_contents(self):
        # PPC D-form RA=0 means literal zero even when r0 contains an address.
        instructions = ["lis r0,4099", "lwz r3,0(r0)", "lwz r4,0(r0)",
                        "lis r9,4099", "ori r9,r9,61440", "stw r3,0(r9)", "blr"]
        with self.assertRaises(ValueError):
            build.inspect_accesses(listing(instructions))


if __name__ == "__main__":
    unittest.main()
