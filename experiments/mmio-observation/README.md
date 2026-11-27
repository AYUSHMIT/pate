# Firmware observation-model audit

This experiment checks a specific modeling assumption in firmware patch
verification: selecting an MMIO address as observable does not by itself give
that address read-to-clear device semantics.

It uses upstream PATE at `284879d8bb3865e0f96e430995b944af2fbf21a0`, an independent
Python transition model, native compiled C execution, and the exact PowerPC32
ELFs executed under QEMU and analyzed by PATE. All work is attributed to the
upstream verifier; the contribution here is the executable audit and evidence.

## Executed result

The [preserved 2026-11-27 evidence](evidence/2026-11-27/README.md) records a
successful build of the **standard, unmodified upstream `exe:pate`**, followed
by all 26 expected comparison outcomes: 15 unconditional equivalences and 11
reported observable differences. There were no conditional, inconclusive,
unsupported, or timed-out cases.

| Validation | Result |
|---|---|
| Reference, runner, and evidence-handling tests | 29/29 passed |
| Upstream `const-global` test selection | 4/4 passed |
| Compiled native executions | 4,160/4,160 passed |
| Exact PowerPC ELFs executed under QEMU | 32/32 passed |
| Standard PATE comparisons | 26/26 matched the independent expectations |

For `q=1`, selecting the second read is equivalent under ordinary memory and
has an observable output difference under the explicit read-to-clear model,
at both optimization levels. For `q=0`, it is equivalent under both models.
The arithmetic control is equivalent in every fixture; the XOR control differs
in every fixture. The snapshot includes raw logs, per-case records, toolchain
provenance, and the exact compiled fixtures with SHA-256 checksums.

## Question and contract

Both programs read a 32-bit status register twice, then publish one output.
The original selects the first returned value; the changed program selects the
second. There are no external updates, interrupts, DMA, or concurrent agents.

| Environment | Reads from initial value `q` | Original output | Changed output |
|---|---|---|---|
| Ordinary memory | `q`, `q` | `q` | `q` |
| Read-to-clear model | `q`, `0` | `q` | `0` |

The equivalent control computes unsigned `(first + 7) - 7`. The deliberately
incorrect control computes `first ^ 1`. PowerPC fixtures initialize the model
explicitly with `q=0` or `q=1`; they do not rely on undocumented ELF-loader
initialization assumptions. Fixtures are compiled at both `-O0` and `-O2`.
The optimized equivalent control may have the same instructions as the original;
it remains an identity control, not an additional distinct transformation.

The native corpus exhausts initial values 0 through 255 and samples four 32-bit
edges. This is **not exhaustive over the 32-bit state space**.

## Observation projections

The ordinary-memory configuration observes status and output, including both
status reads and the common initialization write. Its complete trace therefore
has an initialization prefix before the reference model's three-event core.

The explicit read-to-clear C harness observes output only. Its state-clearing
stores implement internal device-model transitions and are intentionally hidden
from that observation projection. The independent oracle separately specifies
the two logical read events. We do not claim the harness's internal stores are
software-issued writes to a real peripheral.

See [OBSERVATION_MODEL.md](OBSERVATION_MODEL.md) for the source audit, supported
override interfaces, assumption boundaries, and PATE result semantics.

## Reproduce

Requirements: Python 3.10+, a native GCC-compatible C compiler, PowerPC Linux GCC
and binutils, QEMU userspace PowerPC, GHC 9.6.2, Cabal 3.10.2.0, and Yices 2.6.2.
The verifier uses the repository's pinned submodules and Cabal freeze file.
The [audit workflow](../../.github/workflows/mmio-observation.yml) installs these
on an ordinary hosted Linux runner; physical PowerPC hardware is unnecessary.

From the repository root:

```sh
git -c 'url.https://github.com/.insteadOf=git@github.com:' submodule update --init --jobs 4
cp cabal.project.dist cabal.project
cp cabal.GHC-9.6.2.freeze cabal.project.freeze
cabal update
cabal build exe:pate test:pate-test-ppc32 --enable-tests --disable-optimization -j2
cabal test pate-test-ppc32 --disable-optimization --test-show-details=direct \
  --test-options='-p const-global -t 300s'
python3 -m unittest discover -s experiments/mmio-observation/tests -v
python3 experiments/mmio-observation/build.py
pate_binary=$(cabal list-bin exe:pate --disable-optimization)
python3 experiments/mmio-observation/run.py --pate "$pate_binary" \
  --output experiments/mmio-observation/results/run-1
```

For a native/QEMU execution check while the Haskell build is in progress, omit
`--pate` and choose a separate output directory. The summary explicitly records
PATE as `not_run`. Output directories must be empty to prevent evidence from
different runs being mixed.

## Evidence and gates

- `build/manifest.json`: source/binary/configuration/disassembly hashes, exact
  compiler commands, versions, ELF symbol addresses, and access checks.
- `reference-summary.json`: the independently specified bounded comparison matrix.
- `native-results.csv`: every compiled native execution's return value, stored
  output, and final state compared with the independent reference.
- `powerpc-results.json`: actual QEMU execution of each exact PowerPC ELF, with
  its eight-byte output protocol, expected values, and binary hash.
- `pate-results.json`: expected and observed outcomes for two upstream baselines
  and all 24 fixture comparisons, preserving raw result constructors and reasons.
- `pate-logs/`: complete stdout/stderr and the baseline observation configuration.
- `summary.json`: scope, versions, counts, runner/reference hashes, and recursive
  evidence hashes.

The instruction checker is deliberately limited to these straight-line PPC32
fixtures. It requires two resolved 32-bit state loads before one output store and
a return, and rejects unknown memory operations or control flow. Complete
disassemblies remain available for inspection. This is not a general emulator or
an independent proof of instruction-decoder correctness.

PATE process exit zero is insufficient. The runner distinguishes unconditional
equivalence, conditional equivalence, reported observable difference, unsupported
behavior, inconclusive analysis, wall-time timeout, setup failure, and analysis
error. Widening exhaustion, desynchronization, and unresolved assertions cannot
satisfy a negative equivalence test. Raw logs remain the primary result evidence.

## Interpretation

The ordinary-memory analysis establishes equivalence for the second-read patch,
while the explicit stateful model exposes its output difference at `q=1`.
This demonstrates the consequence of the stated environment assumption. It does
not establish a PATE soundness bug, a new verification technique, or behavior of
a physical peripheral. No verifier-core change was needed for this case.

The next research decision depends on whether existing environment-model hooks
are inadequate for a consequential device class. This first audit measures the
specified case before proposing a verifier extension.
