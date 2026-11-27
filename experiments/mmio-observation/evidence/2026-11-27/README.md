# Executed evidence — 2026-11-27

This snapshot records the completed local audit using the **standard upstream
`exe:pate`**, built from PATE commit
`284879d8bb3865e0f96e430995b944af2fbf21a0` with its pinned submodules and
GHC 9.6.2 freeze file. It does not use the temporary PowerPC-only diagnostic
launcher used while the full verifier was compiling.

## Results

| Check | Observed result |
|---|---|
| Reference, runner, and evidence-handling unit tests | 29 passed |
| Upstream `const-global` tests | 4 passed |
| Native executions | 4,160 passed, 0 failed |
| Exact PowerPC ELF executions under QEMU | 32 passed, 0 failed |
| Standard PATE comparisons | 26 expected outcomes, 0 mismatches |

The verifier returned 15 unconditional `Equivalent` results and 11
`Inequivalent` results whose reasons report observable differences. No result
was conditional, unsupported, inconclusive, or timed out. All 26 processes
exited zero, including the 11 differences; the runner classified the terminal
result and its reasons instead of treating process success as equivalence.

| Comparison group | Cases | Equivalent | Observable difference |
|---|---:|---:|---:|
| Upstream equivalent/inequivalent baselines | 2 | 1 | 1 |
| Ordinary memory: arithmetic identity | 4 | 4 | 0 |
| Ordinary memory: select second read | 4 | 4 | 0 |
| Ordinary memory: XOR output with 1 | 4 | 0 | 4 |
| Read-to-clear: arithmetic identity | 4 | 4 | 0 |
| Read-to-clear: select second read, seed 0 | 2 | 2 | 0 |
| Read-to-clear: select second read, seed 1 | 2 | 0 | 2 |
| Read-to-clear: XOR output with 1 | 4 | 0 | 4 |
| **Total** | **26** | **15** | **11** |

The four-case groups cover two initial states and two optimization levels.
The native corpus covers 0 through 255 plus four 32-bit edge values, across
both models, four variants, and both optimization levels. It does not exhaust
the full 32-bit state space.

## What the result establishes

At initial state 1, the ordinary-memory model produces read values `(1, 1)`;
both programs publish 1. The explicit read-to-clear model produces `(1, 0)`;
the original publishes 1 and the second-read patch publishes 0. PATE agrees
with the independent oracle in each analyzed fixture, at both `-O0` and `-O2`.

This confirms a concrete environment-model boundary: marking an address as
observable does not itself provide the peripheral's state transition. The
read-to-clear fixture supplies that transition explicitly in C and hides its
internal model-state writes from the output observation. It does not modify
PATE's memory-read engine or validate a physical MMIO device. The ordinary-memory
trace also includes its shared initialization write; the oracle describes the
subsequent logical read/read/output sequence.

The arithmetic control has distinct add/subtract instructions at `-O0`; its
optimized `-O2` instructions are identical to the original. This is an optimized
identity control. These results establish neither a verifier soundness bug nor
research novelty, and they required no verifier-core change.

## Evidence index

| Artifact | Purpose |
|---|---|
| [standard-cli/summary.json](standard-cli/summary.json) | Completed run, counts, tool versions, verifier and source hashes, recursive evidence hashes |
| [standard-cli/pate-results.json](standard-cli/pate-results.json) | Every command, input/configuration hash, expected result, actual constructor, reasons, and elapsed time |
| [standard-cli/pate-logs/](standard-cli/pate-logs/) | Complete stdout/stderr for all 26 comparisons, plus the baseline observation configuration |
| [standard-cli/native-results.csv](standard-cli/native-results.csv) | All 4,160 native execution rows |
| [standard-cli/powerpc-results.json](standard-cli/powerpc-results.json) | All 32 exact-binary QEMU results and output/state values |
| [standard-cli/reference-summary.json](standard-cli/reference-summary.json) | Independent bounded model expectations |
| [standard-cli/build-manifest.json](standard-cli/build-manifest.json) | Fixture sources, compiler commands, versions, binary/configuration/disassembly hashes, and instruction checks |
| [build-artifacts.tar.gz](build-artifacts.tar.gz) | Exact fixture ELFs, native shared libraries, configurations, disassemblies, ELF headers, and build manifest |
| [unit-tests.json](unit-tests.json), [unit-tests.log](unit-tests.log) | Executed 29-test audit suite and complete output |
| [provenance/baseline-const-global.json](provenance/baseline-const-global.json), [log](provenance/baseline-const-global.log) | Executed four-test upstream baseline selection |
| [provenance/build-result.json](provenance/build-result.json) | Successful standard verifier build, binary hash, and unchanged engine/submodule checks |
| [provenance/environment.json](provenance/environment.json) | Tool versions/hashes, source pins, build command, and freeze hashes |
| [provenance/package-plan.json](provenance/package-plan.json) | Resolved Cabal package plan |
| [provenance/build.log.gz](provenance/build.log.gz) | Complete successful verifier build log, compressed without changing its bytes |
| [provenance/commands.txt](provenance/commands.txt) | Build and upstream test commands |
| [provenance/build-attempt1-missing-gold.log](provenance/build-attempt1-missing-gold.log) | Initial environment failure, resolved by installing the gold linker before the successful unchanged build |
| [provenance/standard-audit.log](provenance/standard-audit.log) | Full final audit command output |
| [SHA256SUMS](SHA256SUMS) | Byte-level hashes of every other file in this snapshot |

## Provenance and replay

The run started from checkout `07816b8719faf0cc2ae7768182b34739f8e72d7d`
with the final audit-runner changes still in the working tree. The recorded
`working_tree_changes` explicitly preserves that fact. The runner SHA-256 is
`68c394c56a60e1bd0354b605c450213cf1d4904bd8b67f4aca13f67c0e04e7f4`,
which matches the `run.py` committed alongside this evidence. Fixture source
hashes and the independent reference hash also match their committed sources.
The verifier engine and submodules had no tracked changes.

The standard PATE executable SHA-256 is
`1061512d84bb4dc857ba1c72a416d9da45fb738e0c70edb312837f519fc6bd87`.
That 310 MB build executable is identified by its hash and reproducible build
inputs rather than stored in Git. The much smaller exact fixture binaries are
included in `build-artifacts.tar.gz`, whose SHA-256 is
`329a2327f86809dfb90068422995b47cce71ac2b3d1c4244c74d110641d914da`.
The archive contains a top-level `build/` directory.

From this snapshot directory, `sha256sum --check SHA256SUMS` verifies all
preserved file bytes. From the repository root, the commands in the
[experiment README](../../README.md#reproduce) rebuild and execute the audit.
To execute the archived fixtures instead of recompiling them, extract
`build-artifacts.tar.gz` into `experiments/mmio-observation/` before running
`run.py` with a fresh output directory and the standard verifier executable.
The runner checks the manifest before execution. Recorded absolute command
paths describe the original environment; they are not required filesystem
locations for reproduction.

This snapshot is local execution evidence. Hosted CI builds and runs the same
audit independently; its results and compiler versions belong to its own run
artifacts. No physical hardware was required for the checks recorded here.
