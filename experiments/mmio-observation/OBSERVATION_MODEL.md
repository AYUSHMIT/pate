# Observation contract and device-model boundary

This experiment asks whether a patch-equivalence conclusion changes when the
environment changes from ordinary memory to an explicitly modeled read-to-clear
register. It audits existing PATE capabilities and supplies a reproducible
example; it does not establish a new verification technique or a hardware bug.
Commands, executed results, and limitations belong in [README.md](README.md).

The source audit uses upstream commit
`284879d8bb3865e0f96e430995b944af2fbf21a0`. Links below refer to that immutable
revision. The mechanisms were checked against the local checkout.

## What PATE already observes

[`PatchData.observableMemory`][config] accepts address ranges through the TOML
`observable-memory` key. Its documentation explicitly identifies MMIO as a use
case. In [`getObservableEvents`][widening], a memory operation is retained when
it is outside the symbolic stack region and overlaps a configured range.
`filterObservableMemOps` does **not** restrict the operation's direction:
configured memory reads and writes are both observable.

[`MemTrace`][memtrace] records each operation's pointer, direction, condition,
width, value, and endianness. `memOpOverlapsRegion` tests address overlap;
`observableEvents` filters the recorded sequence. The verifier compares ordered
sequences through [`doCheckObservables` and `equivalentSequences`][posts].
Observations therefore include intermediate accesses and their order, rather
than only final memory contents. External-call events have separate
observability rules; selecting a memory range does not make every function call
observable.

An observation range selects which events matter. It does not define the
device's transition rules. In `doMemOpInternal`, the ordinary `Read` branch
records the event without changing memory state; the `Write` branch updates
that state. A peripheral that clears a register after reading, consumes a FIFO
entry, or changes asynchronously needs corresponding semantics in the model.

## The initial experiment

Values have the full unsigned 32-bit domain, with no one-bit mask. For a
conceptual initial value `s`, both programs perform two reads:

| Program | Result written to the observed output |
|---|---|
| Original | First read |
| Equivalent control | `(first + 7) - 7`, with unsigned 32-bit wraparound |
| Changed patch | Second read |
| Negative control | First read XOR `1` |

With flat memory, the two reads return `(s, s)`. With the explicit
read-to-clear model, they return `(s, 0)`. Consequently the changed patch has
the same result under flat memory, and differs under read-to-clear whenever
`s != 0`. At `s = 0`, these outputs agree. The arithmetic identity preserves the
output in both models; the XOR control changes it for every input.

The counterexample binary sets the state to `1` in executable code, and a
separate boundary case sets it to `0`. This makes each initial state independent
of an unstated ELF-initialization assumption. A run establishes behavior for
that seeded program; the algebraic statement over all 32-bit values and any
finite oracle checks have separate evidential scope.

The explicit C device harness implements a read-to-clear transition by
reading internal state and then writing zero to it. Those clearing writes
represent device internals, not CPU-visible bus writes. Its observation
configuration therefore excludes the internal state and retains the output.
The omitted state still participates in symbolic execution and determines the
second read. Excluding it from the observed trace does not remove its
semantics.

The flat-memory configuration also observes status, so its complete PATE trace
includes a common initialization `W(status, seed)` before the oracle's
read/read/output core. This common prefix does not change the comparison.

The oracle's logical read/read/output trace and the C harness's output-only
projection are different evidence. Native output/state checks do not validate
physical load events. The flat-memory variant exercises ordinary memory reads
without those state transitions. Its configured observation ranges must be preserved with its
binary and command. Comparing the two variants is a comparison of explicitly
different environment models. The C harness is an executable specification;
it is not an implementation of real peripheral side effects inside PATE's
memory engine.

## Existing extension mechanisms

[`StubOverride`][arch] permits custom symbolic-state transformations;
`StateTransformer2` can operate on the original and patched states together.
Existing helpers include `mkObservableOverride`, `mkReadOverride`, and
`mkWriteOverride`. A future device model can investigate these interfaces
before proposing a new mechanism.

The [`PowerPC override table`][ppc] already assigns modeled output behavior to
`write` and `fwrite`, with an argument-mapping FIXME for `fwrite`. Several other
names, including `read`, `ioctl`, and `FLEXCAN_DRV_Send`, use
`mkDefaultStubOverride`, which supplies a shared arbitrary return value without
a complete model of those APIs' effects. Such assumptions must be recorded for
any experiment that invokes those functions. This fixture should not silently
depend on them.

## Classifying outcomes

[`EquivalenceStatus`][equivalence] distinguishes `Equivalent`,
`Inequivalent [String]`, `ConditionallyEquivalent`, and `Errored`.
The [`native CLI`][main] prints all three non-error results with exit code zero.
**Exit zero alone is not an equivalence result.** Preserve the terminal status,
reason, full logs, configuration, tool revision, and time limit.

[`pairGraphComputeVerdict`][verdict] can return `Inequivalent` for observable
differences, control-flow desynchronization, undischarged assertions, or widening
gas exhaustion. Only the first directly reports an observable counterexample;
its concrete witness still warrants independent checking. Resource exhaustion
and unproved obligations must not become claims of semantic inequivalence.

[`checkSatisfiableWithoutBindings`][monad] turns solver timeouts into `Unknown`;
other solver failures can also reach unknown results. Preserve that uncertainty.
An outer process timeout, unsupported operation, missing dependency, and
unexpected error are distinct outcomes. Keep conditional equivalence and its
conditions separate from unconditional equivalence.

## What would justify further work

This artifact demonstrates how a stated environment assumption affects the
claim being checked. Existing observable-memory support is upstream work. A
new contribution would require a useful device contract, a sound connection
between that contract and binary execution, or another independently validated
result beyond this example. No physical PowerPC execution, real-device
read-to-clear behavior, interrupt handling, DMA, timing guarantee, or whole
firmware assurance follows from this model experiment.

[config]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Config.hs
[widening]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Verification/Widening.hs
[memtrace]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Memory/MemTrace.hs
[posts]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Verification/StrongestPosts.hs
[arch]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Arch.hs
[ppc]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/arch/Pate/PPC.hs
[equivalence]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Equivalence.hs
[main]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/tools/pate/Main.hs
[verdict]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Verification/PairGraph.hs
[monad]: https://github.com/GaloisInc/pate/blob/284879d8bb3865e0f96e430995b944af2fbf21a0/src/Pate/Monad.hs
