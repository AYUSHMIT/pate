#!/usr/bin/env python3
"""Run independent native/PowerPC checks and honestly classify PATE results."""

import argparse
import csv
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import time

import reference

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LIMIT_REASONS = ("Control flow desynchronization at:", "Failed to discharge assertion at:",
                 "Ran out of gas while widening equivalence domains for:")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def prepare_output(output):
    """Never mix evidence from separate runs or overwrite an earlier run."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory is not empty: {output}; choose a new run directory")
    output.mkdir(parents=True, exist_ok=True)


def classify_result(returncode, stdout, stderr, timed_out=False):
    """Classify the final CLI constructor, never mere process success.

    PATE's Inequivalent constructor includes inconclusive analysis reasons.
    Preserve those reasons. A reported observable difference is not labeled
    a physical-hardware counterexample.
    """
    lines = [ANSI.sub("", line).strip() for line in stdout.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    result = {"status": "unclassified", "raw_status": None, "raw_result": last,
              "analysis_limit_reasons": [], "observable_difference_reported": False}
    if timed_out:
        result["status"] = "timeout"
        return result
    if returncode != 0:
        diagnostic = ANSI.sub("", stderr + "\n" + "\n".join(lines[-20:]))
        if re.search(r"\b(Unsupported[A-Za-z]*|unsupported instruction|unsupported architecture|"
                     r"is unsupported|not supported)\b", diagnostic, re.IGNORECASE):
            result["status"] = "unsupported"
        elif re.search(r"\b(ObservabilityError|Unknown|unknown|inconclusive)\b", diagnostic):
            result["status"] = "inconclusive"
        else:
            result["status"] = "analysis_error"
        return result
    if last == "Equivalent":
        result.update(status="equivalent", raw_status="Equivalent")
    elif last == "ConditionallyEquivalent":
        result.update(status="conditionally_equivalent", raw_status="ConditionallyEquivalent")
    elif re.fullmatch(r"Inequivalent \[.*\]", last):
        limits = [reason for reason in LIMIT_REASONS if reason in last]
        observable = "Observable differences at:" in last
        result.update(raw_status="Inequivalent", analysis_limit_reasons=limits,
                      observable_difference_reported=observable)
        result["status"] = "reported_observable_difference" if observable and not limits else "inconclusive"
    return result


def run_process(command, timeout, cwd=REPO):
    started = time.monotonic()
    try:
        process = subprocess.Popen([str(x) for x in command], cwd=cwd, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except OSError as error:
        return {"command": [str(x) for x in command], "returncode": None,
                "stdout": "", "stderr": str(error), "elapsed_seconds": 0,
                "timed_out": False, "setup_error": True}
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    return {"command": [str(x) for x in command], "returncode": process.returncode,
            "stdout": stdout, "stderr": stderr, "elapsed_seconds": round(time.monotonic() - started, 6),
            "timed_out": timed_out, "setup_error": False}


def validate_manifest(build_dir, manifest):
    for path, expected_hash in manifest["sources"].items():
        if sha256(REPO / path) != expected_hash:
            raise ValueError(f"Build source changed since compilation: {path}; rebuild first")
    for item in manifest["binaries"] + manifest["native_libraries"]:
        path = build_dir / item["path"]
        if sha256(path) != item["sha256"]:
            raise ValueError(f"Binary does not match build manifest: {path}")
    for item in manifest["binaries"]:
        if sha256(build_dir / item["config"]) != item["config_sha256"]:
            raise ValueError(f"Observation configuration changed: {item['config']}")
        for name in ("disassembly", "elf_header"):
            if sha256(build_dir / item[name]) != item[name + "_sha256"]:
                raise ValueError(f"Inspection artifact changed: {item[name]}")


def native_checks(build_dir, manifest, output):
    rows = []
    for item in manifest["native_libraries"]:
        library = ctypes.CDLL(str(build_dir / item["path"]))
        run = library.audit_run
        run.argtypes = [ctypes.c_uint32]
        run.restype = ctypes.c_uint32
        state = ctypes.c_uint32.in_dll(library, "audit_state")
        observed = ctypes.c_uint32.in_dll(library, "audit_output")
        for seed in reference.seed_corpus():
            expected = reference.execute(item["model"], item["variant"], seed)
            returned = run(seed)
            passed = returned == observed.value == expected["output"] and state.value == expected["final_state"]
            rows.append({"model": item["model"], "variant": item["variant"], "optimization": item["optimization"],
                         "initial_state": seed, "expected_output": expected["output"], "actual_return": returned,
                         "actual_output": observed.value, "expected_final_state": expected["final_state"],
                         "actual_final_state": state.value, "passed": passed})
    with (output / "native-results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"executions": len(rows), "passed": sum(row["passed"] for row in rows),
            "failed": sum(not row["passed"] for row in rows),
            "checks": ["returned output", "stored output", "final model state"],
            "read_traces": "specified by independent reference; not instrumented in native execution"}


def powerpc_checks(build_dir, manifest, qemu, output):
    rows = []
    for item in manifest["binaries"]:
        expected = reference.execute(item["model"], item["variant"], item["seed"])
        row = {key: item[key] for key in ("model", "variant", "seed", "optimization", "path", "sha256")}
        command = [qemu, str(build_dir / item["path"])]
        try:
            actual = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            values = struct.unpack(">II", actual.stdout) if len(actual.stdout) == 8 else (None, None)
            row.update(command=command, returncode=actual.returncode, stderr=actual.stderr.decode(errors="replace"),
                       stdout_hex=actual.stdout.hex(), actual_output=values[0], actual_final_state=values[1],
                       expected_output=expected["output"], expected_final_state=expected["final_state"],
                       passed=actual.returncode == 0 and values == (expected["output"], expected["final_state"]))
        except (OSError, subprocess.TimeoutExpired) as error:
            row.update(passed=False, error=str(error))
        rows.append(row)
    write_json(output / "powerpc-results.json", rows)
    return {"executions": len(rows), "passed": sum(row["passed"] for row in rows),
            "failed": sum(not row["passed"] for row in rows),
            "engine": "QEMU userspace PowerPC", "physical_hardware": False}


def pate_case(pate, solver, original, patched, config, entry, name, expected_equal, output,
              wall_timeout=120):
    command = [pate, "-o", original, "-p", patched, "-s", entry, "-b", config,
               "--solver", solver, "-e", "ThrowOnAnyFailure", "--goal-timeout", "30", "--heuristic-timeout", "5"]
    result = run_process(command, wall_timeout)
    stdout, stderr = result.pop("stdout"), result.pop("stderr")
    (output / f"{name}.stdout.log").write_text(stdout)
    (output / f"{name}.stderr.log").write_text(stderr)
    result.update(classify_result(result["returncode"], stdout, stderr, result["timed_out"]))
    if result["setup_error"]:
        result["status"] = "setup_error"
    expected_status = "equivalent" if expected_equal else "reported_observable_difference"
    result.update(name=name, expected_equal=expected_equal, expected_status=expected_status,
                  expectation_met=result["status"] == expected_status,
                  original_sha256=sha256(original), patched_sha256=sha256(patched),
                  config_sha256=sha256(config), stdout_log=f"{name}.stdout.log", stderr_log=f"{name}.stderr.log")
    return result


def pate_checks(build_dir, manifest, pate, solver, output, wall_timeout):
    logs = output / "pate-logs"
    logs.mkdir(exist_ok=True)
    baseline_config = logs / "baseline.toml"
    baseline_config.write_text("observable-memory = [{ start-address = 0x1003f000, length = 4 }]\n")
    results = []
    for name, folder, expected in (("upstream-equivalent", "tests/ppc32", True),
                                    ("upstream-inequivalent", "tests/ppc32/unequal", False)):
        result = pate_case(pate, solver, REPO / folder / "const-global.original.exe",
                           REPO / folder / "const-global.patched.exe", baseline_config,
                           "_start", name, expected, logs, wall_timeout)
        results.append(result)
        print(f"{name}: {result['status']} ({result['elapsed_seconds']:.2f}s)", flush=True)
    binaries = {(b["model"], b["optimization"], b["seed"], b["variant"]): b for b in manifest["binaries"]}
    for item in manifest["binaries"]:
        if item["variant"] == "original":
            continue
        original = binaries[(item["model"], item["optimization"], item["seed"], "original")]
        expected = reference.compare(item["model"], item["variant"], item["seed"])["equivalent"]
        name = f"{item['model']}-o{item['optimization']}-s{item['seed']}-{item['variant']}"
        result = pate_case(pate, solver, build_dir / original["path"], build_dir / item["path"],
                           build_dir / item["config"], "audit_entry", name, expected, logs, wall_timeout)
        result.update({k: item[k] for k in ("model", "variant", "seed", "optimization")})
        results.append(result)
        write_json(output / "pate-results.json", results)
        print(f"{name}: {result['status']} ({result['elapsed_seconds']:.2f}s)", flush=True)
    write_json(output / "pate-results.json", results)
    counts = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    solver_path = shutil.which({"Yices": "yices", "Z3": "z3", "CVC4": "cvc4"}[solver])
    return {"cases": len(results), "expectations_met": sum(row["expectation_met"] for row in results),
            "status_counts": counts, "executable_sha256": sha256(pate),
            "solver": solver, "wall_timeout_seconds": wall_timeout,
            "solver_executable": solver_path,
            "solver_version": version([solver_path, "--version"]) if solver_path else None,
            "solver_sha256": sha256(solver_path) if solver_path else None,
            "difference_interpretation": "PATE reported observable differences; independent fixtures establish expected outputs"}


def version(command):
    result = run_process(command, 15)
    return (result["stdout"] or result["stderr"]).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=HERE / "build")
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--qemu", default="qemu-ppc")
    parser.add_argument("--pate", help="Path to the compiled noninteractive exe:pate")
    parser.add_argument("--solver", choices=("Yices", "Z3", "CVC4"), default="Yices")
    parser.add_argument("--wall-timeout", type=int, default=120)
    args = parser.parse_args()
    build_dir, output = args.build_dir.resolve(), args.output.resolve()
    manifest = json.loads((build_dir / "manifest.json").read_text())
    validate_manifest(build_dir, manifest)
    qemu = shutil.which(args.qemu)
    if not qemu:
        parser.error(f"PowerPC execution tool unavailable: {args.qemu}")
    if args.wall_timeout <= 0:
        parser.error("--wall-timeout must be positive")
    pate = shutil.which(args.pate) if args.pate else None
    if args.pate and not pate:
        parser.error(f"PATE executable unavailable: {args.pate}")
    prepare_output(output)
    summary = {"schema_version": 1, "run_status": "in_progress",
               "generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "upstream_commit": manifest["upstream_commit"], "checkout_commit": version(["git", "rev-parse", "HEAD"]),
               "working_tree_changes": version(["git", "status", "--porcelain=v1"]),
               "build_manifest_sha256": sha256(build_dir / "manifest.json"),
               "python": sys.version, "qemu_version": version([qemu, "--version"]),
               "reference_sha256": sha256(HERE / "reference.py"), "runner_sha256": sha256(__file__),
               "scope": "executable ordinary-memory and explicit stateful C models; no physical-device claim"}
    write_json(output / "reference-summary.json", reference.summarize_corpus())
    summary["native"] = native_checks(build_dir, manifest, output)
    summary["powerpc"] = powerpc_checks(build_dir, manifest, qemu, output)
    summary["pate"] = ({"status": "in_progress", "reason": "PATE results are not complete"}
                       if args.pate else {"status": "not_run", "reason": "No --pate executable supplied"})
    write_json(output / "summary.json", summary)
    if args.pate:
        summary["pate"] = pate_checks(build_dir, manifest, str(Path(pate).resolve()),
                                      args.solver, output, args.wall_timeout)
    shutil.copyfile(build_dir / "manifest.json", output / "build-manifest.json")
    summary["evidence_sha256"] = {str(p.relative_to(output)): sha256(p) for p in output.rglob("*")
                                  if p.is_file() and p != output / "summary.json"}
    summary["run_status"] = "completed"
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    success = summary["native"]["failed"] == summary["powerpc"]["failed"] == 0
    if args.pate:
        success = success and summary["pate"]["cases"] == summary["pate"]["expectations_met"]
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
